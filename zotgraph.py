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

class ZotGraph:
    #P_REFS = re.compile("\[(?:\d+,*-*\s*)+\d+\]")
    P_REFS = re.compile("(\[((?:,*-*\s*)?[\w\d\+]+)+])")
    P_REFS2 = re.compile("(\d+(<a href=\".*?\">\w<\/a>){1,3})")
    COLOR_INZOT='#383745'
    #COLOR_REF='#162347'
    #COLOR_CIT='#dd4b39'
    COLOR_DEFAULT_N = '#B1D8F1'
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
        self.nodes = {}
        self.ncachefn = ncache
        self.htmldir = htmldir

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
        self.graph_path = graph_path
        if os.path.exists(graph_path):
            self.__loadGraph()
        else:
            self.g = nx.Graph()

        self.filterfn = filterfn
        self.filterIds = set()
        if os.path.exists(filterfn):
            with open(filterfn, "r") as fd:
                for l in fd.readlines():
                    l = l.rstrip()
                    logging.debug("Add filter %s" % l)
                    self.filterIds.add(l)
    
    def __getNodeInfo(self, paperId):
        smitem = self.nodes[paperId]['smitem']
        author = "?"
        year = 0
        title = "?"
        ncit = -1
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
        return {
            "author": author, 
            "year": year, 
            "ncit": ncit, 
            "title": title,
        }

    def __getJsNode(self, paperId):
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
        ni = self.__getNodeInfo(paperId)

        jsnode = {
                "author": ni["author"],
                "year": ni["year"],   
                "ncit": ni["ncit"],
                "title": ni["title"],
                "node_data": {         
                    "color": self.__getNodeColor(paperId),
                    "id": paperId,
                    "label": self.__getPaperName(paperId),
                    "shape": "box",
                }
            }
        return jsnode
    
    def __loadGraph(self):
        logging.info("Load Graph")
        self.g = nx.read_gpickle(self.graph_path)
        for gnode in self.g.nodes(data=True):
            paperId = gnode[0]
            self.nodes[paperId] = self.__getNode(paperId)
        # clean edges (TODO: remove)
        self.g.remove_edges_from(list(self.g.edges))
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
            logging.debug(node)
            nodes.append(self.__getJsNode(node[0]))
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
                'html': self.__getPaperInfo(node),
            })
        self.lock.release()
        return paperInfo

    def __clearCacheNode(self, paperId):
        if paperId == "":
            logging.error("Empty PaperId")
            return
        ncache_path = os.path.join(self.ncachefn, paperId)
        if os.path.exists(ncache_path):
            os.remove(ncache_path)

    def __storeCacheNode(self, paperId, node, force=False):
        if paperId == "":
            logging.error("Empty PaperId")
            return
        if force or not os.path.exists(os.path.join(self.ncachefn, paperId)):
            with open(os.path.join(self.ncachefn, paperId), "w") as fd:
                fd.write(json.dumps(node, sort_keys=True, indent=2))

    def __loadCacheNode(self, paperId):
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

    def __makeNewNode(self, paperId):
        try:
            logging.info("Searching Semantic Scholar for '%s'" % (paperId))
            smitem = self.sm.paper(paperId)
        except Exception as e:
            logging.error("Failed to get paper '%s' from semanticscholar: %s" % (paperId, e))
            return None

        title = smitem['title']            
        doi = smitem['doi']
        paperId = smitem['paperId']
        logging.info("Searching Zotero for '%s' / '%s'" % (doi, title))
        zaitem = []
        try:
            zaitem = self.za.findItem(key=None, doi=doi, title=title)
        except Exception as e:
            logging.error("Error getting ZaItem for paperId %s: %s" % (paperId, e))

        if zaitem and len(zaitem) > 0:
            zaitem[0]['extref'], zaitem[0]['refinfo'] = self.za.extractRefs(zaitem[0]['data']['key'])
            if zaitem[0]['extref'] and 'titles' in zaitem[0]['extref'].keys():
                for idx, ref_title in zaitem[0]['extref']['titles'].items():
                    if ref_title is None:
                        continue
                    logging.debug("Search for %s" % ref_title)
                    #ref_sms = self.sm.searchTitle(ref_title)
                    #if ref_sms == {}:
                    #    logging.info("Failed to searc for title %s" % (ref_title))
                    #    #logging.error(json.dumps(ref_sms, sort_keys=True, indent=2))
                    #    continue
                    ##logging.info(json.dumps(ref_sms, sort_keys=True, indent=2))
                    #logging.info(ref_sms.keys())
                    best_r = 0
                    best_paperId = None
                    #for ref_sm in ref_sms['data']:
                    for ref_sm in smitem["references"]:
                        r = fuzz.ratio(ref_sm['title'], ref_title)
                        if r > best_r:
                            best_r = r
                            best_paperId = ref_sm['paperId']
                        if r == 100:
                            break
                    zaitem[0]['extref']['paperIds'][idx] = best_paperId

        node = {
            'doi': doi,
            'r_processed': False,
            'c_processed': False,
            'smitem': smitem,
            'zaitem': zaitem,
            'title': title,
        }
        self.__storeCacheNode(paperId, node)

        return node
    
    def __getPaperIdByTitle(self, title):
        if title is None:
            return None
        for paperId, node in self.nodes.items():
            if fuzz.partial_ratio(node['title'].lower(), title.lower()) > 70:
                return paperId
        return None

    def __getAnnotRefs(self, paperId, annots):
        refs_replace = {}
        logging.info("Get annotated references for paperId %s" % paperId)
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
        refs = ZotGraph.P_REFS.findall(annots)
        logging.debug("REFS: %s" % refs)
        for ref_group in refs:
            ref = ref_group[0]
            ref_replace = "%s" % ref
            ref = ref.replace("[", "")
            ref = ref.replace("]", "")
            logging.debug("Scan Ref: %s" % ref)
            replacements = []
            refstr = ref #ref[1:-1]
            refstr_split = []
            if "," in refstr:
                refstr_split = refstr.split(",")
            elif '-' in refstr:
                refp_0, refp_1 = refstr.split("-")
                refstr_split = map(lambda t: "%d" % t, range(refp_0, refp_1))
            else:
                refstr_split = [refstr]

            for refp in refstr_split:
                l_ref = refp
                refp = refp.replace(", ", "")
                refp = refp.replace(" ", "")
                logging.debug("Scan Ref part: '%s'" % refp)
                new_ref = refp
                ref_idx = refp
                if plinks is not None and ref_idx in plinks.keys():
                    ref_url = plinks[ref_idx]
                    if ref_url is not None:
                        logging.debug("Got ref link: %s" % ref_url)
                        l_ref = "<a href=\"%s\" id=\"paperref\">u</a>" % (ref_url)
                        new_ref += l_ref
                if prefs is not None and ref_idx in prefs.keys():
                    ref_title = prefs[ref_idx]
                    logging.debug("Got ref title: %s" % ref_title)
                if refids is not None and ref_idx in refids.keys():
                    ref_paperId = refids[ref_idx]
                    if ref_paperId is not None:
                        logging.debug("Got ref paperId: %s" % ref_paperId)
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

    def __getAnnotations(self, paperId):
        logging.info("Get annotations references for paperId %s" % paperId)
        annots = self.za.getAnnotations(self.nodes[paperId]['zaitem'][0]['key'])[0]
        #logging.info("Got annots for %s: %s" % (paperId, annots))
        ref_replace = self.__getAnnotRefs(paperId, annots)
        for key, replacement in ref_replace.items():
            logging.debug("Replace %s ref %s -> %s" % (paperId, key, replacement))
            annots = annots.replace(key, replacement)
        return annots 

    def __getPaperInfo(self, paperId):
        logging.info("Get paper information for %s" % paperId)
        try:
            abstract = self.nodes[paperId]['smitem']["abstract"] + '\n'
        except Exception as e:
            logging.error("No abstarct for %s: %s" % (paperId, e))
            abstract = "NO ABSTRACT\n"
        try:
            annots = self.__getAnnotations(paperId) + "\n"
        except Exception as e:
            logging.debug("No annots for %s: %s" % (paperId, e))
            annots = "NO ANNOTS\n"
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
        ret += self.__whatDoOthersSay(paperId)

        return ret

    def __getNode(self, paperId):
        node = self.__loadCacheNode(paperId)
        if node == None:
            node = self.__makeNewNode(paperId)
        if node == None:
            logging.error("Failed to get node for %s" % paperId)
            raise("Failed to get node")
        return node
    
    def __whatDoOthersSay(self, paperId):
        if paperId not in self.nodes:
            logging.error("No node for paperid %s")
            return
        
        logging.info("Get Mentions about %s" % paperId)
        ret = ""
        for ref in self.nodes[paperId]['smitem']["citations"]:
            ref_id = ref["paperId"]
            if ref_id not in self.nodes:
                continue
            #logging.info("Check %s" % ref_id)
            try:
                annots = self.__getAnnotations(ref_id)
            except Exception as e:
                #logging.info(e)
                continue

            if not isinstance(annots, str):
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

    def __getPaperName(self, paperId):
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

    def __getColorCollection(self, paperId):
        if 'zaitem' not in self.nodes[paperId] or len(self.nodes[paperId]['zaitem']) == 0:
            return ZotGraph.COLOR_DEFAULT_N
        try:
            zaitem = self.nodes[paperId]['zaitem'][0]
            cols = self.za.getCollections(zaitem)
            colkey = ""
            for _, cname in cols.items():
                colkey += "_%s" % cname
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

    def __getColorAuthor(self, paperId):
        #author, year, ncit, title = self.__getNodeInfo(paperId)
        ni = self.__getNodeInfo(paperId)
        author = ni["author"]
        if author not in self.colcolors.keys():
            try:
                nextcolor = next(self.color[self.coloring])
            except:
                self.color["AUTHOR"] = itertools.chain(iter(plt.cm.tab20c(np.linspace(0, 1, 20))), iter(plt.cm.tab20b(np.linspace(0, 1, 20))))
                nextcolor = next(self.color[self.coloring])
            self.colcolors[author] = matplotlib.colors.rgb2hex(nextcolor)
        return self.colcolors[author]

    def __getColorYear(self, paperId, lightness=0.4):
        #author, year, ncit, title = self.__getNodeInfo(paperId)
        ni = self.__getNodeInfo(paperId)
        year = ni["year"]
        try:
            nextcolor = matplotlib.colors.rgb2hex(self.color[self.coloring].to_rgba(int(year),alpha=lightness))
        except Exception as e:
            logging.error("Error %s" % e)
            nextcolor = "#ffffff"
        #logging.info("Color %s" % nextcolor)
        return nextcolor

    def __getColorNcit(self, paperId, lightness=0.4):
        #author, year, ncit, title = self.__getNodeInfo(paperId)
        ni = self.__getNodeInfo(paperId)
        ncit = ni["ncit"]
        try:
            nextcolor = matplotlib.colors.rgb2hex(self.color[self.coloring].to_rgba(int(ncit),alpha=lightness))
        except Exception as e:
            logging.error("Error %s" % e)
            nextcolor = "#ffffff"
        #logging.info("Color %s" % nextcolor)
        return nextcolor

    def __getNodeColor(self, paperId):
        if paperId not in self.nodes.keys():
            logging.error("Error no node for PaperId '%s'" % paperId)
            return
        #logging.info(self.coloring)
        if self.coloring == "COLLECTION":
            return self.__getColorCollection(paperId)
        if self.coloring == "AUTHOR":
            return self.__getColorAuthor(paperId)
        if self.coloring == "YEAR":
            return self.__getColorYear(paperId)
        if self.coloring == "NCIT":
            return self.__getColorNcit(paperId)
        return ZotGraph.COLOR_DEFAULT_N    

    def __refreshLinks(self, paperId):
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
                new_edges.append(redge)
                
        for ref in node['smitem']['citations']:
            if self.g.has_node(ref['paperId']) and not self.g.has_edge(ref['paperId'], paperId):
                #logging.info("Add citation edge from '%s' <- '%s' " % (self.__getPaperName(paperId), self.__getPaperName(ref['paperId'])))
                redge = self.__addEdge(from_node=ref['paperId'], to_node=paperId, isInfluential=ref['isInfluential'])
                new_edges.append(redge)
        #self.lock.release()
        return new_edges
    
    def refreshAllLinks(self):
        self.lock.acquire()
        for paperId in self.g.nodes:
            self.__refreshLinks(paperId)
        self.lock.release()
        
    def addLinks(self, paperId, influential=False, onlyRef=False, onlyCit=False):
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
                            self.__addNode(ref['doi'], ref['title'], ref['paperId'], pnode=paperId, isRef=True, isInfluential=ref['isInfluential'])
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
                            self.__addNode(ref['doi'], ref['title'], ref['paperId'], pnode=paperId, isRef=False, isInfluential=ref['isInfluential'])
                        for rnode in ref_nodes:
                            new_nodes.append(rnode)
                        for redge in ref_edges:
                            new_edges.append(redge)
                        for rpi in ref_paperinfo:
                            new_paperinfo.append(rpi)
        #node['processed'] = True
        self.lock.release()
        return new_nodes, new_edges, new_paperinfo

    def __filterSMItem(self, paperId):
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

    def addPaperId(self, paperId):
        logging.debug("Add paperId '%s'" % (paperId))
        self.lock.acquire()
        ret = self.__addNode('', '', paperId)
        self.lock.release()
        return ret

    def rescan_all(self):
        self.lock.acquire()
        logging.debug("Rescan All PaperIds")
        self.za.reloadCsv()
        new_nodes = []
        new_edges = []
        new_paperinfo = []
        for paperId in self.g.nodes:
            logging.debug("Rescan PaperId %s" % paperId)
            self.__clearCacheNode(paperId)
            node = self.__getNode(paperId)
            new_nodes.append(self.__getJsNode(paperId))
            new_paperinfo.append({
                'id': paperId,
                'html': self.__getPaperInfo(paperId),
            })
        self.lock.release()
        return new_nodes, new_edges, new_paperinfo

    def rescan(self, paperId):
        self.lock.acquire()
        logging.debug("Rescan PaperId %s" % paperId)
        self.za.reloadCsv()
        self.__clearCacheNode(paperId)
        new_nodes = []
        new_edges = []
        new_paperinfo = []
        node = self.__getNode(paperId)
        new_nodes.append(self.__getJsNode(paperId))
        new_paperinfo.append({
            'id': paperId,
            'html': self.__getPaperInfo(paperId),
        })
        self.lock.release()
        return new_nodes, new_edges, new_paperinfo

    def __removePaperId(self, paperId):
        logging.debug("Remove PaperId '%s'" % (paperId))
        self.filterIds.add(paperId)
        with open(self.filterfn, "w") as fd:
            for fid in self.filterIds:
                fd.write(fid + "\n")
        try:
            self.g.remove_node(paperId)
        except:
            pass

    def removePaperId(self, paperId):
        self.lock.acquire()
        self.__removePaperId(paperId)
        self.lock.release()
    
    def __addEdge(self, from_node=None, to_node=None, isInfluential=False, edgeColor=COLOR_INZOT):
        if from_node is None or to_node is None:
            logging.error("Can not add edge to none %s/%s" % (from_node, to_node))
            return
        ni_from = self.__getNodeInfo(from_node)
        ni_to = self.__getNodeInfo(to_node)
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

    def __addNode(self, doi, title, paperId, pnode=None, isRef=False, isInfluential=False, edgeColor=COLOR_INZOT):
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
                    new_edges.append(self.__addEdge(from_node=pnode, to_node=paperId, isInfluential=isInfluential, edgeColor=edgeColor))
                else:
                    new_edges.append(self.__addEdge(from_node=paperId, to_node=pnode, isInfluential=isInfluential, edgeColor=edgeColor))
            return new_nodes, new_edges, new_paperinfo

        node = self.__getNode(paperId)
        self.nodes[paperId] = node

        if self.__filterSMItem(paperId):
            logging.debug("add filter '%s' / '%s' / '%s'" % (doi, title, paperId))
            self.__removePaperId(paperId)
            return new_nodes, new_edges, new_paperinfo
    
        label = self.__getPaperName(paperId)
        color = self.__getNodeColor(paperId)
        logging.debug("Add node '%s' / '%s'" % (paperId, label))
        self.g.add_node(paperId, label=label, shape='box', color=color)
        new_nodes.append(self.__getJsNode(paperId))
        new_paperinfo.append({
            'id': paperId,
            'html': self.__getPaperInfo(paperId),
        })
        if pnode:
            if isRef:
                new_edges.append(self.__addEdge(from_node=pnode, to_node=paperId, isInfluential=isInfluential, edgeColor=edgeColor))
            else:
                new_edges.append(self.__addEdge(from_node=paperId, to_node=pnode, isInfluential=isInfluential, edgeColor=edgeColor))

        refreshed_edges = self.__refreshLinks(paperId)
        new_edges.extend(refreshed_edges)
        return new_nodes, new_edges, new_paperinfo