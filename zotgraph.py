from pyzotero import zotero
import logging
FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
logging.basicConfig(format=FORMAT)
logging.basicConfig(format=FORMAT, level=logging.INFO)

import itertools
import networkx as nx
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
                    logging.info("ADD FILTER %s" % l)
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
        return author, year, ncit, title

    def getJsNode(self, paperId):
        if paperId in self.filterIds:
            logging.info("PaperId '%s' is filtered" % paperId)
            return {}
        if paperId not in self.nodes.keys():
            logging.err("No Node for PaperId '%s'" % paperId)
            return {}
        if 'smitem' not in self.nodes[paperId]:
            logging.info("No Semantic Scholar entry for PaperId '%s'" % paperId)
            return {}

        author, year, ncit, title = self.__getNodeInfo(paperId)

        jsnode = {
                "author": author,
                "year": year,   
                "ncit": ncit,
                "title": title,
                "node_data": {         
                    "color": self.getNodeColor(paperId),
                    "id": paperId,
                    "label": self.getPaperName(paperId),
                    "shape": "box",
                }
            }
        return jsnode

    def saveGraph(self):
        logging.info("save graph to %s" % self.graph_path)
        nx.write_gpickle(self.g, self.graph_path)

    def __loadGraph(self):
        logging.info("Load Graph")
        self.g = nx.read_gpickle(self.graph_path)
        for gnode in self.g.nodes(data=True):
            paperId = gnode[0]
            self.nodes[paperId] = self.__getNode(paperId)

    def getGraph(self):
        logging.info("Get Graph")
        nodes = []
        edges = []
        added_nodes = set()
        for node in self.g.nodes(data=True):
            logging.info(node)
            nodes.append(self.getJsNode(node[0]))
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
        paperInfo = []
        for node in self.g.nodes:
            paperInfo.append({
                'id': node,
                'html': self.__getPaperInfo(node),
            })
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
            logging.info("Loading Cached Node %s" % paperId)
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

    def __getAnnotRefs(self, paperId):
        refs_replace = {}
        logging.info("Ref paper %s" % paperId)
        annots = self.za.getAnnotations(self.nodes[paperId]['zaitem'][0]['key'])[0]
        prefs = None
        plinks = None
        try:
            prefs = self.nodes[paperId]['zaitem'][0]['extref']["titles"]
            plinks = self.nodes[paperId]['zaitem'][0]['extref']["links"]
        except Exception as e:
            logging.info("No extracted annotations for %s: %s" % (paperId, e))
            logging.info(json.dumps(self.nodes[paperId]['zaitem'][0]))
            return {}
        p = re.compile("\[(?:\d+,*\s*)+\d+\]")
        refs = p.findall(annots)
        for ref in refs:
            replacements = []
            #logging.info("Ref: %s" % ref)
            refstr = ref[1:-1]
            for refp in refstr.split(", "):
                new_ref = refp
                #l_scholar = "%ss" % refp
                #l_graph = "%sg" % refp
                #l_ref = "%su" % refp
                ref_idx = int(refp) - 1
                if plinks is not None and len(plinks) > int(ref_idx):
                    ref_url = plinks[ref_idx]
                    if ref_url is not None:
                        l_ref = "<a href=\"%s\" id=\"paperref\">u</a>" % (ref_url)
                        new_ref += l_ref
                if prefs is not None and len(prefs) > int(ref_idx):
                    ref_title = prefs[ref_idx]
                    logging.info(ref_title)
                    ref_paperId = self.__getPaperIdByTitle(ref_title)
                    if ref_paperId is not None:
                        ref_linkurl = "https://www.semanticscholar.org/paper/%s" % ref_paperId
                        l_scholar = "<a href=\"%s\" id=\"paperref\">s</a>" % (ref_linkurl)
                        new_ref += l_scholar
                        l_graph = "<a href=\"javascript:highlight_edge(\'%s\', \'%s\');\" id=\"paperref\">g</a>" % (paperId, ref_paperId)
                        new_ref += l_graph
                replacements.append(new_ref)

            refs_replace[ref] = "[%s]" % ", ".join(replacements)
        return refs_replace

    def __getPaperInfo(self, paperId):
        try:
            abstract = self.nodes[paperId]['smitem']["abstract"] + '\n'
            #logging.info("Got abstarct for %s: %s" % (paperId, abstract))
        except Exception as e:
            logging.info("No abstarct for %s: %s" % (paperId, e))
            abstract = "NO ABSTRACT\n"
        try:
            annots = self.za.getAnnotations(self.nodes[paperId]['zaitem'][0]['key'])[0] + '\n'
            #logging.info("Got annots for %s: %s" % (paperId, annots))
            ref_replace = self.__getAnnotRefs(paperId)
            for key, replacement in ref_replace.items():
                logging.info("Replace %s ref %s -> %s" % (paperId, key, replacement))
                annots = annots.replace(key, replacement)
        except Exception as e:
            logging.info("No annots for %s: %s" % (paperId, e))
            annots = "NO ANNOTS\n"
        linkurl = "https://www.semanticscholar.org/paper/%s" % paperId
        ret = '<p>\n'
        ret += '<a href="%s">%s</a>\n' % (linkurl, linkurl)
        ret += '</p>\n'
        ret += '<p>\n'
        ret += abstract
        ret += '</p>\n'
        ret += '<p>\n'
        ret += annots
        ret += '</p>\n'

        return ret

    def __getNode(self, paperId):
        node = self.__loadCacheNode(paperId)
        if node == None:
            node = self.__makeNewNode(paperId)
        if node == None:
            logging.error("Failed to get node for %s" % paperId)
            raise("Failed to get node")
        return node

    def getPaperName(self, paperId):
        try:
            smitem = self.nodes[paperId]['smitem']
            return "%s - %d - %s" % (smitem['year'], len(smitem["citations"]), smitem['title'])
        except:
            return paperId

    def setColoring(self, coloring):
        if coloring not in ZotGraph.COLORINGS:
            logging.err("Invalid coloring")
            return False
        self.coloring = coloring
        #logging.info("Coloring: %s" % self.coloring)
        self.colcolors = {}
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
            logging.info("Failed to get color for %s: %s" % (paperId, e))
            logging.info(zaitem)
            raise("XXX")
            return ZotGraph.COLOR_DEFAULT_N

    def __getColorAuthor(self, paperId):
        author, year, ncit, title = self.__getNodeInfo(paperId)
        if author not in self.colcolors.keys():
            try:
                nextcolor = next(self.color[self.coloring])
            except:
                self.color["AUTHOR"] = itertools.chain(iter(plt.cm.tab20c(np.linspace(0, 1, 20))), iter(plt.cm.tab20b(np.linspace(0, 1, 20))))
                nextcolor = next(self.color[self.coloring])
            self.colcolors[author] = matplotlib.colors.rgb2hex(nextcolor)
        return self.colcolors[author]

    def __getColorYear(self, paperId, lightness=0.4):
        author, year, ncit, title = self.__getNodeInfo(paperId)
        try:
            nextcolor = matplotlib.colors.rgb2hex(self.color[self.coloring].to_rgba(int(year),alpha=lightness))
        except Exception as e:
            logging.error("Error %s" % e)
            nextcolor = "#ffffff"
        #logging.info("Color %s" % nextcolor)
        return nextcolor

    def __getColorNcit(self, paperId, lightness=0.4):
        author, year, ncit, title = self.__getNodeInfo(paperId)
        try:
            nextcolor = matplotlib.colors.rgb2hex(self.color[self.coloring].to_rgba(int(ncit),alpha=lightness))
        except Exception as e:
            logging.error("Error %s" % e)
            nextcolor = "#ffffff"
        #logging.info("Color %s" % nextcolor)
        return nextcolor

    def getNodeColor(self, paperId):
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
        
    def addLinks(self, paperId, influential=False, onlyRef=False, onlyCit=False):
        new_nodes = []
        new_edges = []
        new_paperinfo = []
        logging.info("Add links for PaperId '%s'" % paperId)
        if paperId in self.filterIds:
            logging.info("PaperId '%s' is filtered" % paperId)
            return new_nodes, new_edges, new_paperinfo
        if paperId not in self.nodes.keys():
            logging.error("Error no node for PaperId '%s'" % paperId)
            return new_nodes, new_edges, new_paperinfo
        node = self.nodes[paperId]
        #if node['processed']:
        #    logging.info("PaperId '%s' is already processed" % paperId)
        #    return new_nodes, new_edges, new_paperinfo
        if node['smitem'] is not None:
            if not onlyCit and not node['r_processed']:
                for ref in node['smitem']['references']:
                    if not influential or ref['isInfluential']:
                        ref_nodes, ref_edges, ref_paperinfo = \
                            self.__addNode(ref['doi'], ref['title'], ref['paperId'], pnode=paperId, isRef=False, isInfluential=ref['isInfluential'])
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
                            self.__addNode(ref['doi'], ref['title'], ref['paperId'], pnode=paperId, isRef=True, isInfluential=ref['isInfluential'])
                        for rnode in ref_nodes:
                            new_nodes.append(rnode)
                        for redge in ref_edges:
                            new_edges.append(redge)
                        for rpi in ref_paperinfo:
                            new_paperinfo.append(rpi)
        #node['processed'] = True
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
        logging.info("Add paperId '%s'" % (paperId))
        return self.__addNode('', '', paperId)

    def rescan(self, paperId):
        logging.info("Rescan PaperId %s" % paperId)
        self.za.reloadCsv()
        self.__clearCacheNode(paperId)
        new_nodes = []
        new_edges = []
        new_paperinfo = []
        node = self.__getNode(paperId)
        new_nodes.append(self.getJsNode(paperId))
        new_paperinfo.append({
            'id': paperId,
            'html': self.__getPaperInfo(paperId),
        })
        return new_nodes, new_edges, new_paperinfo

    def removePaperId(self, paperId):
        logging.info("Remove PaperId '%s'" % (paperId))
        self.filterIds.add(paperId)
        with open(self.filterfn, "w") as fd:
            for fid in self.filterIds:
                fd.write(fid + "\n")
        try:
            self.g.remove_node(paperId)
        except:
            pass
    
    def __addEdge(self, cit_node, ref_node, isInfluential, edgeColor=COLOR_INZOT):
        weight = 1
        edgeColor = '#bdc9c4'
        if isInfluential:
            weight = 6
            edgeColor = '#2e5361'
        self.g.add_edge(ref_node, cit_node, color=edgeColor, weight=weight)
        return {
            "from": ref_node,
            "to": cit_node,
            "color": edgeColor,
            "weight": weight,
        }

    def __addNode(self, doi, title, paperId, pnode=None, isRef=False, isInfluential=False, edgeColor=COLOR_INZOT):
        new_nodes = []
        new_edges = []
        new_paperinfo = []
        logging.info("Add paper '%s' / '%s' / '%s'" % (doi, title, paperId))
        if paperId is not None and paperId in self.filterIds:
            logging.info("PaperId '%s' is filtered" % paperId)
            return new_nodes, new_edges, new_paperinfo
        if paperId is not None and paperId in self.nodes.keys():
            logging.info("PaperId '%s' is already present" % paperId)
            if pnode:
                if isRef:
                    new_edges.append(self.__addEdge(pnode, paperId, isInfluential, edgeColor))
                else:
                    new_edges.append(self.__addEdge(paperId, pnode, isInfluential, edgeColor))
            return new_nodes, new_edges, new_paperinfo

        node = self.__getNode(paperId)
        self.nodes[paperId] = node

        if self.__filterSMItem(paperId):
            logging.info("add filter '%s' / '%s' / '%s'" % (doi, title, paperId))
            self.removePaperId(paperId)
            return new_nodes, new_edges, new_paperinfo
    
        label = self.getPaperName(paperId)
        color = self.getNodeColor(paperId)
        logging.info("Add node '%s' / '%s'" % (paperId, label))
        self.g.add_node(paperId, label=label, shape='box', color=color)
        new_nodes.append(self.getJsNode(paperId))
        new_paperinfo.append({
            'id': paperId,
            'html': self.__getPaperInfo(paperId),
        })
        if pnode:
            if isRef:
                new_edges.append(self.__addEdge(pnode, paperId, isInfluential, edgeColor))
            else:
                new_edges.append(self.__addEdge(paperId, pnode, isInfluential, edgeColor))

        return new_nodes, new_edges, new_paperinfo