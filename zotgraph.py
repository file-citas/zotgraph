from pyzotero import zotero
import logging
#FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
#logging.basicConfig(format=FORMAT)
#logging.basicConfig(format=FORMAT, level=logging.INFO)

import itertools
import networkx as nx
import threading
import os
import re
import json
import pickle
import matplotlib.pyplot as plt
import matplotlib
import numpy as np
from fuzzywuzzy import fuzz
from fuzzywuzzy import process
from semanticscholar import SemanticScholar
from zotapi import ZotApi
from datetime import timedelta
from ratelimit import limits, sleep_and_retry
from refextract import RefExtract

PAPER_DEDUPS= {
    "57cee3a90bb0caa822fc188b083a01aa1e17cca9": "d896ef2a393eb8022446a7d8951432ac8f424bbd",
    "fd0427a143ee4d17fd07dee0faff24c853081d99": "a64cbe93930b51276af3c5235dad2b8d6d7aef67",
    "88ad913424405ac32657a8557f74003b22e9be3c": "aa5be948f04bb23fa6473157312413df3cbbc44e",
    "c9cfafe6655cf84ee5c3f1924b2e03839634ea60": "81afe3f238b7ec01e30346bb476e3d29af9683aa",
    "2148c6ae13180dbc4e7aec3a56f41abd66c1784a": "f97154903f5b00b07f31623fa2e7ba8d81982762",
    "b0ff249b9b507fb973f007656598a19fc6e9b287": "65e946633ddf39084ec9c37e00e05b89ed424d39",
}
PAPER_DEDUPS_INV = {v: k for k, v in PAPER_DEDUPS.items()}
# Available values : acs, ama, apa, chicago, ensemble, experimental, harvard, ieee, mhra, mla, nature, vancouver


CITATION_STYLES = {
    "0fc4415291af1e74f23dfcf3ba3ab192c6649a79": 'apa'
}


def dedup_paper(func):
    def wrapper(*args, **kwargs):
        if "paperId" in kwargs.keys() and kwargs['paperId'] in PAPER_DEDUPS.keys():
            logging.info("Deduplicate paperId %s -> %s" % (kwargs["paperId"], PAPER_DEDUPS[kwargs["paperId"]]))
            kwargs["paperId"] = PAPER_DEDUPS[kwargs["paperId"]]
        return func(*args,**kwargs)
    return wrapper


class ZotGraph:
    MIN_TITLE_LEN = 16
    FUZZ_TITLE_MINR = 65
    NO_ANNOTS_STR = "NO ANNOTATIONS"
    #P_REFS = re.compile("\[(?:\d+,*-*\s*)+\d+\]")
    P_REFS = re.compile("(\[((?:,*-*\s*)?[\w\d\+]+)+])")
    P_REFS_TEST = re.compile("(\[(.*?)\])")
    P_REFS2 = re.compile("(\d+(<a href=\".*?\">\w<\/a>){1,3})")
    COLOR_INZOT='#383745'
    #COLOR_REF='#162347'
    #COLOR_CIT='#dd4b39'
    COLOR_DEFAULT_N = '#cccccc' #'#B1D8F1'
    COLORINGS = set(["COLLECTION", "YEAR", "NCIT", "AUTHOR"])


    def __init__(self, graph_path, cfilter, htmldir, ncache, filterfn, libcsv, library_id, library_type, api_key):
        logging.info("FILTER CONF: %s" % json.dumps(cfilter, sort_keys=True, indent=2))
        logging.info("HTML DIR: %s" % htmldir)
        logging.info("NODE CAHCE: %s" % ncache)
        logging.info("FILTER: %s" % filterfn)
        self.lock = threading.Lock()
        self.max_dist = cfilter['dist']
        self.min_year = cfilter['year']
        self.max_cit = cfilter['cit']
        self.za = ZotApi(libcsv, library_id, library_type, api_key)
        self.sm = SemanticScholar()
        self.re = RefExtract(self.sm)
        self.nodes = {}
        self.ncachefn = ncache
        self.htmldir = htmldir
        self.year_to_paperid = {}
        self.c_min_year = 2050 #self.min_year
        self.c_max_year = 0
        self.c_min_ncit = 10000
        self.c_max_ncit = 0 #self.max_cit

        #self.color = iter(cm.rainbow(np.linspace(0,1,32)))
        #self.color = iter(cm.Pastel1(np.linspace(0,1,32)))
        self.coloring = "COLLECTION"

        self.color = {}
        self.color["COLLECTION"] = itertools.chain(iter(plt.cm.tab20c(np.linspace(0, 1, 20))), iter(plt.cm.tab20b(np.linspace(0, 1, 20))))
        self.color["AUTHOR"] = itertools.chain(iter(plt.cm.tab20c(np.linspace(0, 1, 20))), iter(plt.cm.tab20b(np.linspace(0, 1, 20))))
        seqmap = plt.get_cmap('summer')
        norm_year = matplotlib.colors.Normalize(vmin=self.min_year, vmax=2021)
        self.color["YEAR"] = plt.cm.ScalarMappable(cmap=seqmap, norm=norm_year)
        norm_ncit = matplotlib.colors.Normalize(vmin=0, vmax=40) #self.max_cit)
        self.color["NCIT"] = plt.cm.ScalarMappable(cmap=seqmap, norm=norm_ncit)
        #self.color["NCIT"] = plt.cm.PuBuGn(np.linspace(0, 1.0, 100))
        self.colcolors = {}
        self.filterfn = filterfn
        self.filterIds = set()
        if os.path.exists(filterfn):
            with open(filterfn, "r") as fd:
                for l in fd.readlines():
                    l = l.rstrip()
                    logging.debug("Add filter %s" % l)
                    self.filterIds.add(l)
        
        self.graph_path = graph_path
        if os.path.exists(graph_path):
            self.__loadGraph()
        else:
            self.g = nx.DiGraph()
    
    @dedup_paper
    def __getNodeInfo(self, paperId=None):
        smitem = self.nodes[paperId]['smitem']
        author = "?"
        year = 0
        title = "?"
        ncit = -1
        collection = "N/A"
        try:
            author = smitem['authors'][0]['name']
        except:
            pass
        try:
            year = smitem['year']
        except:
            pass
        try:
            title = smitem['title']
        except:
            pass
        try:
            ncit = len(smitem['citations'])
        except:
            pass
        if 'zaitem' in self.nodes[paperId].keys() and len(self.nodes[paperId]['zaitem']) > 0:
            zaitem = self.nodes[paperId]['zaitem'][0]
            cnames = []
            for col in zaitem['data']['collections']:
                cnames.append(self.za.getCollectionName(col))
            collection = ":".join(cnames)
            #collection = self.za.getCollectionName(zaitem['data']['collections'])
            #collection = self.za.getCollectionNameByKey(zaitem['key'])

        return {
            "author": author, 
            "year": year, 
            "ncit": ncit, 
            "collection": collection,
            "title": title,
        }

    @dedup_paper
    def __getJsNode(self, paperId=None):
        if paperId in self.filterIds:
            logging.info("PaperId '%s' is filtered" % paperId)
            return {}
        if paperId not in self.nodes.keys():
            logging.err("No Node for PaperId '%s'" % paperId)
            return {}
        if 'smitem' not in self.nodes[paperId]:
            logging.info("No Semantic Scholar entry for PaperId '%s'" % paperId)
            return {}

        #author, year, ncit, title = self.__getNodeInfo(paperId)
        ni = self.__getNodeInfo(paperId=paperId)

        jsnode = {
                "author": ni["author"],
                "year": ni["year"],   
                "ncit": ni["ncit"],
                "collection": ni["collection"],
                "title": ni["title"],
                "node_data": {         
                    "color": self.__getNodeColor(paperId=paperId),
                    "id": paperId,
                    "label": self.__getPaperName(paperId=paperId),
                    "shape": "box",
                }
            }
        try:
            jsnode['node_data']['level'] = "%d" % self.year_to_level[int(ni['year'])]
        except Exception as e:
            logging.error("Can not get year for %s: %s" % (paperId, e))
            #logging.error(json.dumps(self.year_to_level, sort_keys=True, indent=2))
            jsnode['node_data']['level'] = "1"
        return jsnode
    
    @dedup_paper
    def __loadGraph(self):
        logging.info("Load Graph")
        G = nx.read_gpickle(self.graph_path)
        self.g = nx.DiGraph()
        #self.g.add_nodes_from(G.nodes(data=True))
        all_nodes = set()
        for gnode in G.nodes(data=True):
            if gnode[0] not in self.filterIds and \
                gnode[0] not in all_nodes and \
                gnode[0] not in PAPER_DEDUPS.keys():
                self.g.add_node(gnode[0], **gnode[1])
                all_nodes.add(gnode[0])
                paperId = gnode[0]
                self.nodes[paperId] = self.__getNode(paperId=paperId)
            
        # clean edges (TODO: remove)
        #self.g.remove_edges_from(list(self.g.edges))
        self.refreshAllLinks()

    def saveGraph(self):
        logging.info("save graph to %s" % self.graph_path)
        nx.write_gpickle(self.g, self.graph_path)


    def getGraph(self):
        logging.info("Get Graph")
        nodes = []
        edges = []
        added_nodes = set()
        for node in self.g.nodes(data=True):
            if node[0] not in added_nodes and node[0] not in PAPER_DEDUPS.keys():
                logging.debug(node)
                gnode = self.__getJsNode(paperId=node[0])
                nodes.append(gnode)
                added_nodes.add(node[0])
        for edge in self.g.edges(data=True):
            if edge[0] not in added_nodes or edge[1] not in added_nodes:
                continue
            edges.append({
                "color": edge[2]["color"],
                "from": edge[0],
                "to": edge[1],
            })
        return nodes, edges
    
    def getPaperInfo(self):
        self.lock.acquire()
        paperInfo = []
        for node in self.g.nodes:
            paperInfo.append({
                'id': node,
                'html': self.__getPaperInfo(paperId=node),
            })
        self.lock.release()
        return paperInfo

    @dedup_paper
    def __clearCacheNode(self, paperId=None):
        if paperId == "":
            logging.error("Empty PaperId")
            return
        ncache_path = os.path.join(self.ncachefn, paperId)
        if os.path.exists(ncache_path):
            os.remove(ncache_path)

    @dedup_paper
    def __storeCacheNode(self, paperId=None, node=None, force=False):
        if paperId == "":
            logging.error("Empty PaperId")
            return
        if force or not os.path.exists(os.path.join(self.ncachefn, paperId)):
            with open(os.path.join(self.ncachefn, paperId), "w") as fd:
                fd.write(json.dumps(node, sort_keys=True, indent=2))

    @dedup_paper
    def __loadCacheNode(self, paperId=None):
        if paperId == "":
            logging.error("Empty PaperId")
            return
        if not os.path.exists(os.path.join(self.ncachefn, paperId)):
            return None
        with open(os.path.join(self.ncachefn, paperId), "r") as fd:
            logging.debug("Loading Cached Node %s" % paperId)
            node = json.loads(fd.read())
            node['r_processed'] = False
            node['c_processed'] = False
            return node

    def __extractRefs(self, key, paperId=None):
        try:
            pdfpath = self.za.getPdfPath(key)
        except Exception as e:
            logging.warn("Could not get pdf path for key '%s': %s" % (key, e))
            return  {}, {}
        if not pdfpath:
            logging.warn("No pdf for %s" % key)
            return {}, {}
        return self.re.extractRefs(pdfpath, paperId)

    def __findTitleSemanticScholar(self, title, all_refIds):
        logging.info("Searching Semantic Scholar '%s'" % title)
        sm_data = self.sm.searchTitle(title)
        #logging.info(json.dumps(sm_data, sort_keys=True, indent=2))
        for sm_entry in sm_data["data"]:
            if sm_entry["paperId"] not in all_refIds:
                continue
            r = fuzz.partial_ratio(title.lower(), sm_entry["title"].lower())
            logging.info("SM title r %d '%s'" % (r, sm_entry["title"]))
            if r > 80:
                logging.info("Best SM title '%s'" % sm_entry["title"])
                return sm_entry["title"]
        return "?"


    def __cluster_rs(self, all_rs, c_diff=32, verbose=False):
        r_diffs = [0]
        s_rs = sorted(list(all_rs))

        if verbose:
            logging.info(s_rs)
        for i in range(0, len(s_rs)-1):
            r_diffs.append(-1*(s_rs[i]-s_rs[i+1]))
        
        if verbose:
            logging.info(r_diffs)

        clusters = {}
        cluster = []
        for i in range(0, len(s_rs)):
            if r_diffs[i] >= c_diff:
                if verbose:
                    logging.info("ADD Cluster %d: %s" % (s_rs[i], str(cluster)))
                clusters[s_rs[i-1]] = cluster
                cluster = []
            cluster.append(s_rs[i])
        if len(cluster) > 0:
            clusters[s_rs[-1]] = cluster
        if verbose:
            logging.info(json.dumps(clusters, sort_keys=True, indent=2))
        return clusters
        
    @dedup_paper
    def __matchTitle(self, ref_title, smitem):
        if len(ref_title) < ZotGraph.MIN_TITLE_LEN:
            return 0, "", ""
        logging.debug("Search for %s" % ref_title)
        best_r = 0
        best_paperId = None
        best_title = ""
        candidates = {}
        all_rs = set()
        for ref_sm in smitem["references"]:
            if len(ref_sm['title']) < ZotGraph.MIN_TITLE_LEN:
                continue
            r = fuzz.partial_ratio(ref_sm['title'].lower(), ref_title.lower())
            all_rs.add(r)
            if r not in candidates.keys():
                candidates[r] = []
            if r > best_r:
                best_r = r
                best_paperId = ref_sm['paperId']
                best_title = ref_sm['title']
            candidates[r].append({
                "t": ref_sm['title'],
                "id": ref_sm['paperId'],
            })
            #if r == 100:
            #    break
        clusters = self.__cluster_rs(all_rs)
        return best_r, best_paperId, best_title, candidates
        
    @dedup_paper
    def __makeNewNode(self, paperId=None):

        logging.info("Searching Semantic Scholar for '%s'" % (paperId))
        smitem = self.sm.paper(paperId)
        #try:
        #    logging.info("Searching Semantic Scholar for '%s'" % (paperId))
        #    smitem = self.sm.paper(paperId)
        #except Exception as e:
        #    logging.error("Failed to get paper '%s' from semanticscholar: %s" % (paperId, e))
        #    return None

        title = smitem['title']            
        doi = smitem['doi']
        paperId = smitem['paperId']
        logging.info("Searching Zotero for '%s' / '%s'" % (doi, title))
        zaitem = []
        try:
            zaitem = self.za.findItem(key=None, doi=doi, title=title)
            logging.info("Got Zotero item (%d) for '%s' / '%s'" % (len(zaitem), doi, title))
        except Exception as e:
            logging.error("Error getting ZaItem for paperId %s: %s" % (paperId, e))


        all_refIds = set()
        for ref in smitem["references"]:
            all_refIds.add(ref['paperId'])
        if zaitem and len(zaitem) > 0:
            zaitem[0]['extref'], zaitem[0]['refinfo'] = self.__extractRefs(zaitem[0]['data']['key'], paperId=paperId)
            new_titles = {}
            if zaitem[0]['extref'] and 'titles' in zaitem[0]['extref'].keys():
                for idx, ref_title in zaitem[0]['extref']['titles'].items():
                    new_titles[idx] = ref_title
                    if ref_title is None:
                        continue
                    if ref_title == "?":
                        continue
                    if len(ref_title) < ZotGraph.MIN_TITLE_LEN:
                        continue
                    logging.debug("Search for %s" % ref_title)
                    best_r, best_paperId, best_title, candidates = self.__matchTitle(ref_title, smitem)
                    for cand in candidates[best_r]:
                        logging.info("Best candidates score %d: '%s' / %s" % (best_r, cand['t'], cand['id']))
                    if best_r > ZotGraph.FUZZ_TITLE_MINR:
                        logging.info("Fuzzy matched title score %d '%s' / '%s'" % (best_r, ref_title, best_title))
                        zaitem[0]['extref']['paperIds'][idx] = best_paperId
                        new_titles[idx] = best_title
                    else:
                        #if len(candidates[best_r]) == 1:
                        #    logging.info("Fuzzy matched best candidate group: score %d '%s' / '%s'" % (best_r, ref_title, best_title))
                        #else:
                        logging.info("Fuzzy matched title failed (trying sm): score %d '%s' / '%s'" % (best_r, ref_title, best_title))
                        #sm_title = self.__findTitleSemanticScholar(ref_title, all_refIds)
                        #best_r, best_paperId, best_title, candidates = self.__matchTitle(sm_title, smitem)
                        #if best_r > ZotGraph.FUZZ_TITLE_MINR:
                        #    logging.info("Fuzzy matched SM title score %d '%s' / '%s'" % (best_r, ref_title, best_title))
                        #    zaitem[0]['extref']['paperIds'][idx] = best_paperId
                        #    new_titles[idx] = best_title
                        #else:
                        #    logging.info("Fuzzy matched SM title failed: score %d '%s' / '%s'" % (best_r, sm_title, best_title))

        node = {
            'doi': doi,
            'r_processed': False,
            'c_processed': False,
            'smitem': smitem,
            'zaitem': zaitem,
            'title': title,
        }
        self.__storeCacheNode(paperId=paperId, node=node)

        return node
    
    def __getPaperIdByTitle(self, title):
        if title is None:
            return None
        for paperId, node in self.nodes.items():
            if fuzz.partial_ratio(node['title'].lower(), title.lower()) > 70:
                return paperId
        return None

    @dedup_paper
    def __getAnnotRefs(self, paperId=None, annots=None):
        verbose = False
        #if paperId == "0fc4415291af1e74f23dfcf3ba3ab192c6649a79":
        #    verbose = True
        refs_replace = {}
        logging.debug("Get annotated references for paperId %s" % paperId)
        #annots = self.za.getAnnotations(self.nodes[paperId]['zaitem'][0]['key'])[0]

        prefs = None
        plinks = None
        refids = None
        try:
            prefs = self.nodes[paperId]['zaitem'][0]['extref']["titles"]
            plinks = self.nodes[paperId]['zaitem'][0]['extref']["links"]
            refids = self.nodes[paperId]['zaitem'][0]['extref']["paperIds"]
        except Exception as e:
            logging.debug("No extracted annotations for %s: %s" % (paperId, e))
            logging.debug(json.dumps(self.nodes[paperId]['zaitem'][0]))
            return {}
        #logging.debug(annots)
        refs = ZotGraph.P_REFS_TEST.findall(annots)
        if verbose:
            logging.info("REFS: %s" % refs)
        for ref_group in refs:
            ref = ref_group[0]
            ref_replace = "%s" % ref
            ref = ref.replace("[", "")
            ref = ref.replace("]", "")
            if verbose:
                logging.info("Scan Ref: %s" % ref)
            replacements = []
            refstr = ref #ref[1:-1]
            refstr_split = []
            if "," in refstr:
                refstr_split = refstr.split(",")
            elif '-' in refstr:
                refp_0, refp_1 = refstr.split("-")
                refstr_split = map(lambda t: "%d" % t, range(int(refp_0), int(refp_1)))
            else:
                refstr_split = [refstr]

            for refp in refstr_split:
                l_ref = refp
                refp = refp.replace(", ", "")
                #refp = refp.replace(" ", "")
                refp = refp.removeprefix(" ")
                refp = refp.removesuffix(" ")
                if verbose:
                    logging.info("Scan Ref part: '%s'" % refp)
                    logging.info(plinks.keys())
                new_ref = refp
                ref_idx = refp
                if plinks is not None and ref_idx in plinks.keys():
                    ref_url = plinks[ref_idx]
                    if ref_url is not None:
                        if verbose:
                            logging.info("Got ref link: %s" % ref_url)
                        l_ref = "<a href=\"%s\" id=\"paperref\">u</a>" % (ref_url)
                        new_ref += l_ref
                if prefs is not None and ref_idx in prefs.keys():
                    ref_title = prefs[ref_idx]
                    if verbose:
                        logging.info("Got ref title: %s" % ref_title)
                if refids is not None and ref_idx in refids.keys():
                    ref_paperId = refids[ref_idx]
                    if ref_paperId is not None:
                        if verbose:
                            logging.info("Got ref paperId: %s" % ref_paperId)
                        ref_linkurl = "https://www.semanticscholar.org/paper/%s" % ref_paperId
                        l_scholar = "<a href=\"%s\" id=\"paperref\">s</a>" % (ref_linkurl)
                        new_ref += l_scholar
                        if self.g.has_node(ref_paperId):
                            l_graph = "<a href=\"javascript:highlight_edge(\'%s\', \'%s\');\" id=\"paperref\">g</a>" % (paperId, ref_paperId)
                            new_ref += l_graph
                replacements.append(new_ref)

            refs_replace[ref_replace] = "[%s]" % ", ".join(replacements)
        
        logging.debug("Got annotated references for paperId %s" % paperId)
        return refs_replace

    @dedup_paper
    def __getAnnotations(self, paperId=None):
        verbose = False
        #if paperId == "0fc4415291af1e74f23dfcf3ba3ab192c6649a79":
        #    verbose = True
        logging.debug("Get annotations references for paperId %s" % paperId)
        annots = ZotGraph.NO_ANNOTS_STR
        zaitem = None
        try:
            zaitem = self.nodes[paperId]['zaitem']
            zaitem = zaitem[0]
        except (KeyError, IndexError):
            logging.debug("No zotero item for paperId %s" % paperId)
            return annots
        
        if zaitem is not None:
            annots_cand = self.za.getAnnotations(zaitem['key'])[0]
            if isinstance(annots_cand, str):
                annots = annots_cand
                #logging.info("Got annots for %s: %s" % (paperId, annots))
                ref_replace = self.__getAnnotRefs(paperId=paperId, annots=annots)
                for key, replacement in ref_replace.items():
                    if verbose:
                        logging.debug("Replace %s ref %s -> %s" % (paperId, key, replacement))
                    annots = annots.replace(key, replacement)
        
        logging.debug("Got annotations references for paperId %s" % paperId)
        return annots 

    @dedup_paper
    def __getPaperInfo(self, paperId=None):
        logging.debug("Get paper information for %s" % paperId)
        try:
            abstract = self.nodes[paperId]['smitem']["abstract"] + '\n'
        except Exception as e:
            logging.error("No abstarct for %s: %s" % (paperId, e))
            abstract = "NO ABSTRACT\n"
        annots = self.__getAnnotations(paperId=paperId) + "\n"
        linkurl = "https://www.semanticscholar.org/paper/%s" % paperId
        ret = '<p>\n'
        ret += '<a href="%s">%s</a>\n' % (linkurl, linkurl)
        ret += '</p>\n'
        ret += '<h2>%s</h2>\n' % self.nodes[paperId]['title']
        ret += '<p>\n'
        ret += abstract
        ret += '</p>\n'
        ret += '<h2>Annotations</h2>\n'
        ret += '<p>\n'
        ret += annots
        ret += '</p>\n'

        ret += '<h2>Mentions</h2>\n'
        ret += self.__whatDoOthersSay(paperId=paperId)

        logging.debug("Got paper information for %s" % paperId)
        return ret

    def __updateYearSpan(self):
        years_sorted = sorted(list(self.year_to_paperid.keys()))
        new_min = years_sorted[0]
        new_max = years_sorted[-1]
        changed = False
        if new_min < self.c_min_year:
            logging.info("Adjusting year range min %d -> %d:%d" % (self.c_min_year, new_min, self.c_max_year))
            self.c_min_year = new_min
            changed = True
        if new_max > self.c_max_year:
            logging.info("Adjusting year range max %d: %d -> %d" % (self.c_min_year, self.c_max_year, new_max))
            self.c_max_year = new_max
            changed = True
        if changed:
            seqmap = plt.get_cmap('summer')
            norm_year = matplotlib.colors.Normalize(vmin=self.c_min_year, vmax=self.c_max_year)
            self.color["YEAR"] = plt.cm.ScalarMappable(cmap=seqmap, norm=norm_year)
        self.year_to_level = {t: i+1 for i, t in enumerate(sorted(self.year_to_paperid.keys()))}

    def __updateYearSpanRemove(self, node):
        try:
            y = int(node['smitem']['year'])
        except Exception as e:
            logging.error("Failed to update year span on removing %s: %s" % (node['smitem']['paperId'], e))
            return
        if y not in self.year_to_paperid.keys():
            logging.error("Can not remove year %d" % y)
            return

        if node['smitem']['paperId'] not in self.year_to_paperid[y]:
            logging.error("PaperId %d not part of year %d" % (node['smitem']['paperId'], y))
            return

        self.year_to_paperid[y].remove(node['smitem']['paperId'])
        if len(self.year_to_paperid[y]) == 0:
            logging.info("Removing year %d" % y)
            del self.year_to_paperid[y]
        self.__updateYearSpan()

    def __updateYearSpanAdd(self, node):
        try:
            y = int(node['smitem']['year'])
        except Exception as e:
            logging.error("Failed to update year span on adding %s: %s" % (node['smitem']['paperId'], e))
            return
        if y not in self.year_to_paperid.keys():
            logging.info("Add year %d" % y)
            self.year_to_paperid[y] = set()
        self.year_to_paperid[y].add(node['smitem']['paperId'])
        self.__updateYearSpan()

    @dedup_paper
    def __getNode(self, paperId=None):
        node = self.__loadCacheNode(paperId=paperId)
        if node == None:
            node = self.__makeNewNode(paperId=paperId)
        if node == None:
            logging.error("Failed to get node for %s" % paperId)
            raise("Failed to get node")
        node = self.__dedupRefs(node)
        self.__updateYearSpanAdd(node)

        try:
            nc = len(node['smitem']['citations'])
            if nc > self.c_max_nc:
                self.c_max_ncit = nc
                seqmap = plt.get_cmap('summer')
                norm_ncit = matplotlib.colors.Normalize(vmin=self.c_min_ncit, vmax=self.c_max_ncit)
                self.color["NCIT"] = plt.cm.ScalarMappable(cmap=seqmap, norm=norm_ncit)
            if nc < self.c_min_nc:
                self.c_min_nc = nc
                seqmap = plt.get_cmap('summer')
                norm_ncit = matplotlib.colors.Normalize(vmin=self.c_min_ncit, vmax=self.c_max_ncit)
                self.color["NCIT"] = plt.cm.ScalarMappable(cmap=seqmap, norm=norm_ncit)
        except:
            pass
        return node

    def __getNodeNoDedup(self, paperId=None):
        node = self.__loadCacheNode(paperId=paperId)
        if node == None:
            node = self.__makeNewNode(paperId=paperId)
        if node == None:
            logging.error("Failed to get node for %s" % paperId)
            raise("Failed to get node")
        return node

    @dedup_paper    
    def __whatDoOthersSay(self, paperId=None):
        if paperId not in self.nodes:
            logging.error("No node for paperid %s")
            return
        
        logging.debug("Get Mentions about %s" % paperId)
        ret = ""
        handled = set()
        for ref in self.nodes[paperId]['smitem']["citations"]:
            ref_id = ref["paperId"]
            if ref_id not in self.nodes:
                continue
            if ref_id in handled:
                continue
            handled.add(ref_id)
            annots = self.__getAnnotations(paperId=ref_id)
            
            if not isinstance(annots, str) or annots == ZotGraph.NO_ANNOTS_STR:
                continue
            ret_paras = ""
            for para in annots.split("<p>"):
                if paperId in para:
                    refs = ZotGraph.P_REFS2.findall(annots)
                    for ref in refs:
                        if paperId in ref[0]:
                            para = para.replace(ref[0], "<strong>%s</strong>" % ref[0])
                    ret_paras += "<p>" + para
            if ret_paras != "":
                ret += "<p><strong>%s</strong> says: </p> %s" % (self.nodes[ref_id]["title"], ret_paras)
        return ret

    @dedup_paper
    def __getPaperName(self, paperId=None):
        try:
            smitem = self.nodes[paperId]['smitem']
            return "%s - %d - %s" % (smitem['year'], len(smitem["citations"]), smitem['title'])
        except:
            return paperId

    def setColoring(self, coloring):
        if coloring not in ZotGraph.COLORINGS:
            logging.error("Invalid coloring")
            return False
        self.lock.acquire()
        self.coloring = coloring
        #logging.info("Coloring: %s" % self.coloring)
        self.colcolors = {}
        self.lock.release()
        return True

    @dedup_paper
    def __getColorCollection(self, paperId=None):
        if 'zaitem' not in self.nodes[paperId] or len(self.nodes[paperId]['zaitem']) == 0:
            return ZotGraph.COLOR_DEFAULT_N
        try:
            zaitem = self.nodes[paperId]['zaitem'][0]
            cols = zaitem['data']['collections']
            cols.sort()
            colkey = "_".join(cols)
            #cols = self.za.getCollections(zaitem)
            #colkey = ""
            #for _, cname in cols.items():
            #    colkey += "_%s" % cname
            if colkey not in self.colcolors.keys():
                try:
                    nextcolor = next(self.color[self.coloring])
                except:
                    self.color["COLLECTION"] = itertools.chain(iter(plt.cm.tab20c(np.linspace(0, 1, 20))), iter(plt.cm.tab20b(np.linspace(0, 1, 20))))
                    nextcolor = next(self.color[self.coloring])
                self.colcolors[colkey] = matplotlib.colors.rgb2hex(nextcolor)
            return self.colcolors[colkey]
        except Exception as e:
            logging.error("Failed to get color for %s: %s" % (paperId, e))
            logging.error(zaitem)
            raise("XXX")
            return ZotGraph.COLOR_DEFAULT_N

    @dedup_paper
    def __getColorAuthor(self, paperId=None):
        #author, year, ncit, title = self.__getNodeInfo(paperId)
        ni = self.__getNodeInfo(paperId=paperId)
        author = ni["author"]
        if author not in self.colcolors.keys():
            try:
                nextcolor = next(self.color[self.coloring])
            except:
                self.color["AUTHOR"] = itertools.chain(iter(plt.cm.tab20c(np.linspace(0, 1, 20))), iter(plt.cm.tab20b(np.linspace(0, 1, 20))))
                nextcolor = next(self.color[self.coloring])
            self.colcolors[author] = matplotlib.colors.rgb2hex(nextcolor)
        return self.colcolors[author]

    @dedup_paper
    def __getColorYear(self, paperId=None, lightness=0.4):
        #author, year, ncit, title = self.__getNodeInfo(paperId)
        ni = self.__getNodeInfo(paperId=paperId)
        year = ni["year"]
        try:
            nextcolor = matplotlib.colors.rgb2hex(self.color[self.coloring].to_rgba(int(year),alpha=lightness))
        except Exception as e:
            logging.error("Error %s" % e)
            nextcolor = "#ffffff"
        #logging.info("Color %s" % nextcolor)
        return nextcolor

    @dedup_paper
    def __getColorNcit(self, paperId=None, lightness=0.4):
        #author, year, ncit, title = self.__getNodeInfo(paperId)
        ni = self.__getNodeInfo(paperId=paperId)
        ncit = ni["ncit"]
        try:
            nextcolor = matplotlib.colors.rgb2hex(self.color[self.coloring].to_rgba(int(ncit),alpha=lightness))
        except Exception as e:
            logging.error("Error %s" % e)
            nextcolor = "#ffffff"
        #logging.info("Color %s" % nextcolor)
        return nextcolor

    @dedup_paper
    def __getNodeColor(self, paperId=None):
        if paperId not in self.nodes.keys():
            logging.error("Error no node for PaperId '%s'" % paperId)
            return
        #logging.info(self.coloring)
        if self.coloring == "COLLECTION":
            return self.__getColorCollection(paperId=paperId)
        if self.coloring == "AUTHOR":
            return self.__getColorAuthor(paperId=paperId)
        if self.coloring == "YEAR":
            return self.__getColorYear(paperId=paperId)
        if self.coloring == "NCIT":
            return self.__getColorNcit(paperId=paperId)
        return ZotGraph.COLOR_DEFAULT_N    

    def __dedupRefs(self, node):
        if node['smitem'] is None:
            return
        for i, ref in enumerate(node['smitem']['references']):
            if ref['paperId'] in PAPER_DEDUPS.keys():
                logging.debug("Replace duplicate ref for %s: %s -> %s" % (node['smitem']['paperId'], ref['paperId'], PAPER_DEDUPS[ref['paperId']]))
                node['smitem']['references'][i]['paperId'] = PAPER_DEDUPS[ref['paperId']]
        for i, ref in enumerate(node['smitem']['citations']):
            if ref['paperId'] in PAPER_DEDUPS.keys():
                logging.debug("Replace duplicate cit for %s: %s -> %s" % (node['smitem']['paperId'], ref['paperId'], PAPER_DEDUPS[ref['paperId']]))
                node['smitem']['citations'][i]['paperId'] = PAPER_DEDUPS[ref['paperId']]
        if node['smitem']['paperId'] in PAPER_DEDUPS_INV.keys():
            dedup_node = self.__getNodeNoDedup(PAPER_DEDUPS_INV[node['smitem']['paperId']])
            logging.debug("Merge %d citations and %d references for duplicate node %s / %s" % \
                (len(dedup_node['smitem']['citations']), len(dedup_node['smitem']['references']), 
                node['smitem']['paperId'], dedup_node['smitem']['paperId']))
            cit_handled = set()
            for ref in dedup_node['smitem']['citations']:
                if ref['paperId'] in cit_handled:
                    continue
                cit_handled.add(ref['paperId'])
                node['smitem']['citations'].append(ref)
            ref_handled = set()
            for ref in dedup_node['smitem']['references']:
                if ref['paperId'] in ref_handled:
                    continue
                ref_handled.add(ref['paperId'])
                node['smitem']['citations'].append(ref)
            #node['smitem']['references'].extend(dedup_node['smitem']['references'])
        return node

    @dedup_paper
    def __refreshLinks(self, paperId=None):
        #self.lock.acquire()
        new_edges = []
        if paperId not in self.nodes.keys():
            return
        node = self.nodes[paperId]
        if node['smitem'] is None:
            return
        for ref in node['smitem']['references']:
            if self.g.has_node(ref['paperId']) and not self.g.has_edge(paperId, ref['paperId']):
                #logging.info("Add reference edge from '%s' -> '%s' " % (self.__getPaperName(paperId), self.__getPaperName(ref['paperId'])))
                redge = self.__addEdge(from_node=paperId, to_node=ref['paperId'], isInfluential=ref['isInfluential'])
                if redge is not None:
                    new_edges.append(redge)
                
        for ref in node['smitem']['citations']:
            if self.g.has_node(ref['paperId']) and not self.g.has_edge(ref['paperId'], paperId):
                #logging.info("Add citation edge from '%s' <- '%s' " % (self.__getPaperName(paperId), self.__getPaperName(ref['paperId'])))
                redge = self.__addEdge(from_node=ref['paperId'], to_node=paperId, isInfluential=ref['isInfluential'])
                if redge is not None:
                    new_edges.append(redge)
        #self.lock.release()
        return new_edges
    
    def refreshAllLinks(self):
        self.lock.acquire()
        for paperId in self.g.nodes:
            self.__refreshLinks(paperId=paperId)
        self.lock.release()

    @dedup_paper        
    def addLinks(self, paperId=None, influential=False, onlyRef=False, onlyCit=False):
        self.lock.acquire()
        new_nodes = []
        new_edges = []
        new_paperinfo = []
        logging.debug("Add links for PaperId '%s'" % paperId)
        if paperId in self.filterIds:
            logging.debug("PaperId '%s' is filtered" % paperId)
            self.lock.release()
            return new_nodes, new_edges, new_paperinfo
        if paperId not in self.nodes.keys():
            logging.error("Error no node for PaperId '%s'" % paperId)
            self.lock.release()
            return new_nodes, new_edges, new_paperinfo
        node = self.nodes[paperId]

        if node['smitem'] is not None:
            if not onlyCit and not node['r_processed']:
                for ref in node['smitem']['references']:
                    if not influential or ref['isInfluential']:
                        ref_nodes, ref_edges, ref_paperinfo = \
                            self.__addNode(ref['doi'], ref['title'], paperId=ref['paperId'], pnode=paperId, isRef=True, isInfluential=ref['isInfluential'])
                        for rnode in ref_nodes:
                            new_nodes.append(rnode)
                        for redge in ref_edges:
                            new_edges.append(redge)
                        for rpi in ref_paperinfo:
                            new_paperinfo.append(rpi)
            if not onlyRef and not node['c_processed']:
                for ref in node['smitem']['citations']:
                    if not influential or ref['isInfluential']:
                        ref_nodes, ref_edges, ref_paperinfo = \
                            self.__addNode(ref['doi'], ref['title'], paperId=ref['paperId'], pnode=paperId, isRef=False, isInfluential=ref['isInfluential'])
                        for rnode in ref_nodes:
                            new_nodes.append(rnode)
                        for redge in ref_edges:
                            new_edges.append(redge)
                        for rpi in ref_paperinfo:
                            new_paperinfo.append(rpi)
        #node['processed'] = True
        self.lock.release()
        return new_nodes, new_edges, new_paperinfo

    @dedup_paper
    def __filterSMItem(self, paperId=None):
        try:
            smitem = self.nodes[paperId]['smitem']
        except:
            logging.error("No smitem for %s" % paperId)
            return True
        try:
            if paperId in self.filterIds:
                logging.info("Filter '%s' static" % (smitem['title']))
                return True
        except:
            pass
        try:
            if int(smitem['year']) < self.min_year:
                logging.info("Filter '%s' year %d < %d" % (smitem['title'], int(smitem['year']), self.min_year))
                return True
        except:
            pass
        try:
            if len(smitem['citations']) > self.max_cit:
                logging.info("Filter '%s' citc %d < %d" % (smitem['title'], len(smitem['citations']), self.max_cit))
                return True
        except:
            pass
        return False

    @dedup_paper
    def addPaperId(self, paperId=None):
        logging.debug("Add paperId '%s'" % (paperId))
        self.lock.acquire()
        ret = self.__addNode('', '', paperId=paperId)
        self.lock.release()
        return ret
    
    def addCollectionId(self, colname):
        logging.debug("Add collection '%s'" % (colname))
        new_nodes = []
        new_edges = []
        new_paperInfo = []
        self.lock.acquire()
        skeys = self.za.getCollectionItemsByName(colname)
        logging.info("Got keys for collection %s: %s" % (colname, ", ".join(skeys)))
        for skey in skeys:
            nn, ne, np = self.__addNode("", "", paperId=skey)
            new_nodes.extend(nn)
            new_edges.extend(ne)
            new_paperInfo.extend(np)
        self.lock.release()
        return new_nodes, new_edges, new_paperInfo

    @dedup_paper
    def rescan_all(self):
        self.lock.acquire()
        logging.debug("Rescan All PaperIds")
        self.za.reloadCsv()
        new_nodes = []
        new_edges = []
        new_paperinfo = []
        for paperId in self.g.nodes:
            logging.debug("Rescan PaperId %s" % paperId)
            self.__clearCacheNode(paperId=paperId)
            node = self.__getNode(paperId=paperId)
            new_nodes.append(self.__getJsNode(paperId=paperId))
            new_paperinfo.append({
                'id': paperId,
                'html': self.__getPaperInfo(paperId=paperId),
            })
        self.lock.release()
        return new_nodes, new_edges, new_paperinfo

    @dedup_paper
    def rescan(self, paperId=None):
        self.lock.acquire()
        logging.debug("Rescan PaperId %s" % paperId)
        self.za.reloadCsv()
        self.__clearCacheNode(paperId=paperId)
        new_nodes = []
        new_edges = []
        new_paperinfo = []
        node = self.__getNode(paperId=paperId)
        new_nodes.append(self.__getJsNode(paperId=paperId))
        new_paperinfo.append({
            'id': paperId,
            'html': self.__getPaperInfo(paperId=paperId),
        })
        self.lock.release()
        return new_nodes, new_edges, new_paperinfo

    @dedup_paper
    def __removePaperId(self, paperId=None):
        logging.debug("Remove PaperId '%s'" % (paperId))
        self.filterIds.add(paperId)
        with open(self.filterfn, "w") as fd:
            for fid in self.filterIds:
                fd.write(fid + "\n")

        self.__updateYearSpanRemove(self.nodes[paperId])
        if self.g.has_node(paperId):
            self.g.remove_node(paperId)
        if paperId in self.nodes.keys():
            del self.nodes[paperId]

    @dedup_paper
    def removePaperId(self, paperId=None):
        self.lock.acquire()
        self.__removePaperId(paperId=paperId)
        self.lock.release()

    def __addEdge(self, from_node=None, to_node=None, isInfluential=False, edgeColor=COLOR_INZOT):
        if from_node == to_node:
            logging.error("Edge %s - %s self reference" % (from_node, to_node))
            return None
        if self.g.has_edge(from_node, to_node) or self.g.has_edge(to_node, from_node):
            logging.debug("Edge %s - %s already exists" % (from_node, to_node))
            return None
        debug_edge = False
        if (from_node == "a299bd8d1d1b7e44273f1d517d6032f93ec7fcbf" or to_node == "a299bd8d1d1b7e44273f1d517d6032f93ec7fcbf") and \
            (from_node == "439280d09bdc02c13fe2f2dc5eff1e145a16cc45" or to_node == "439280d09bdc02c13fe2f2dc5eff1e145a16cc45"):
            debug_edge = True
        if from_node is None or to_node is None:
            logging.error("Can not add edge to none %s/%s" % (from_node, to_node))
            return
        
        if debug_edge:
            logging.info("ADD EDGE %s -> %s" % (self.__getPaperName(from_node), self.__getPaperName(to_node)))
        ni_from = self.__getNodeInfo(paperId=from_node)
        ni_to = self.__getNodeInfo(paperId=to_node)
        year_from = None
        year_to = None
        try:
            year_from = int(ni_from["year"])
        except Exception as e:
            logging.error("No year for from '%s'" % ni_from['title'])
        try:
            year_to = int(ni_to["year"])
        except Exception as e:
            logging.error("No year for to '%s'" % ni_to['title'])
        if year_from is not None and year_to is not None and year_from < year_to:
            logging.error("Corrupted edge %d -> %d, '%s' -> '%s'" % (year_from, year_to, ni_from['title'], ni_to['title']))
        weight = 1
        edgeColor = '#bdc9c4'
        if isInfluential:
            weight = 6
            edgeColor = '#2e5361'
        self.g.add_edge(from_node, to_node, color=edgeColor, weight=weight)
        return {
            "from": from_node,
            "to": to_node,
            "color": edgeColor,
            "weight": weight,
        }

    @dedup_paper
    def __addNode(self, doi, title, paperId=None, pnode=None, isRef=False, isInfluential=False, edgeColor=COLOR_INZOT):
        new_nodes = []
        new_edges = []
        new_paperinfo = []
        logging.debug("Add paper '%s' / '%s' / '%s'" % (doi, title, paperId))
        if paperId is not None and paperId in self.filterIds:
            logging.debug("PaperId '%s' is filtered" % paperId)
            return new_nodes, new_edges, new_paperinfo
        if paperId is not None and paperId in self.nodes.keys():
            logging.debug("PaperId '%s' is already present" % paperId)
            if pnode:
                if isRef:
                    redge = self.__addEdge(from_node=pnode, to_node=paperId, isInfluential=isInfluential, edgeColor=edgeColor)
                    if redge is not None:
                        new_edges.append(redge)
                else:
                    redge = self.__addEdge(from_node=paperId, to_node=pnode, isInfluential=isInfluential, edgeColor=edgeColor)
                    if redge is not None:
                        new_edges.append(redge)
            return new_nodes, new_edges, new_paperinfo

        node = self.__getNode(paperId=paperId)
        self.nodes[paperId] = node

        if self.__filterSMItem(paperId=paperId):
            logging.debug("add filter '%s' / '%s' / '%s'" % (doi, title, paperId))
            self.__removePaperId(paperId=paperId)
            return new_nodes, new_edges, new_paperinfo
    
        label = self.__getPaperName(paperId=paperId)
        color = self.__getNodeColor(paperId=paperId)
        logging.info("Add node '%s' / '%s'" % (paperId, label))
        self.g.add_node(paperId, label=label, shape='box', color=color)
        new_nodes.append(self.__getJsNode(paperId=paperId))
        new_paperinfo.append({
            'id': paperId,
            'html': self.__getPaperInfo(paperId=paperId),
        })
        if pnode:
            if isRef:
                redge = self.__addEdge(from_node=pnode, to_node=paperId, isInfluential=isInfluential, edgeColor=edgeColor)
                if redge is not None:
                    new_edges.append(redge)
            else:
                redge = self.__addEdge(from_node=paperId, to_node=pnode, isInfluential=isInfluential, edgeColor=edgeColor)
                if redge is not None:
                    new_edges.append(redge)

        refreshed_edges = self.__refreshLinks(paperId=paperId)
        new_edges.extend(refreshed_edges)
        return new_nodes, new_edges, new_paperinfo