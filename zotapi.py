from pyzotero import zotero
import pandas as pd
import logging

from refextract import RefExtract


class ZotApi:
    def __init__(self, libcsv, library_id, library_type, api_key):
        self.zot = zotero.Zotero(library_id, library_type, api_key)
        self.df = pd.read_csv(libcsv)
        self.libcsv = libcsv
        self.colkeys = {}
        logging.info("Initialized Zotero API csv: %s, libid: %s, libtype: %s, akey: %s" % (libcsv, library_id, library_type, api_key))

    def reloadCsv(self):
        self.df = pd.read_csv(self.libcsv)

    def isValidDOI(doi):
        if doi and doi.startswith("10."):
            return True
        return False

    def getItemIdByTitle(self, title):
        #logging.info("Searching Zotero for title '%s'" % title)
        return self.df[self.df['Title'] == title]['Key'].values

    def getItemByTitle(self, title):
        logging.info("Search Zotero for title '%s'" % title)
        if title is None:
            return []
        keys = self.getItemIdByTitle(title)
        if len(keys) == 0:
            logging.warn("Invalid Zotero title (not found) '%s'" % title)
            return []
        if len(keys) > 1:
            logging.warn("Invalid Zotero title (mutliple entries %s) '%s'" % (title, len(keys)))
            return []
        logging.info("Got Zotero keys '%s' for title '%s'" % (", ".join(keys), title))
        return self.zot.top(itemKey=keys[0])

    def getItemIdByDOI(self, doi):
        return self.df[self.df['DOI'] == doi]['Key'].values

    def getItemByDOI(self, doi):
        logging.info("Searching Zotero for doi '%s'" % doi)
        if doi is None:
            return []
        keys = self.getItemIdByDOI(doi)
        if len(keys) == 0 or len(keys) > 1:
            logging.info("Invalid doi %s" % doi)
            return []
        try:
            logging.debug("Got Zotero keys '%s' for doi '%s'" % (", ".join(keys), doi))
            return self.zot.top(itemKey=keys[0])
        except Exception as e:
            logging.err("Could not get Zotero item by doi '%s': %s'" % (doi,e ))
        return []

    def getItemByKey(self, key):
        return self.zot.top(itemKey=key)

    def findItem(self, key=None, doi=None, title=None):
        ret = []
        if key is not None:
            ret = self.getItemByKey(key)
        if len(ret) == 0 and doi is not None and ZotApi.isValidDOI(doi):
            ret = self.getItemByDOI(doi)
        if len(ret) == 0 and title is not None:
            ret = self.getItemByTitle(title)
        return ret

    def getAnnotations(self, key):
        logging.info("get annotations for key %s" % key)
        annots = self.df[self.df['Key'] == key]['Notes'].values
        if len(annots) < 1 or not isinstance(annots[0], str):
            logging.warn("No annotations for %s" % key)
            raise Exception("No annots for key %s" % key)
        #logging.info("Got annots for %s: %s" % (key, annots))
        #ref_replace = self.__getAnnotRefs(paperId, annots)
        #for key, replacement in ref_replace.items():
        #    logging.debug("Replace %s ref %s -> %s" % (paperId, key, replacement))
        #    annots = annots.replace(key, replacement)
        return annots

    def extractRefs(self, key):
        try:
            pdfpath = self.getPdfPath(key)
        except Exception as e:
            logging.warn("Could not get pdf path for key '%s': %s" % (key, e))
            return  {}, {}
        if not pdfpath:
            logging.warn("No pdf for %s" % key)
            return {}, {}
        return RefExtract.extractRefs(pdfpath)

        
    def getPdfPath(self, key):
        logging.debug("get pdf path for key %s" % key)
        files = self.df[self.df['Key'] == key]['File Attachments'].values
        if len(files) == 1:
            for fn in files[0].split("; "):
                if fn.endswith(".pdf"):
                    return fn
        return None


    def __getParentCollectionNames(self, ckey):
        col = self.zot.collection(ckey)
        cname = col['data']['name']
        pcolkey = col['data']['parentCollection']
        parents = [cname]
        if pcolkey:
            pcol = self.__getParentCollectionNames(pcolkey)
            parents.extend(pcol)
        return parents


    def getCollections(self, item):
        ckeys = item['data']['collections']
        if len(ckeys) == 0:
            return None
        cols = {}
        for ckey in ckeys:
            if ckey not in self.colkeys.keys():
                self.colkeys[ckey] = self.__getParentCollectionNames(ckey)
            cols[ckey] = self.colkeys[ckey]
        return cols
