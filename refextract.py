import logging
import subprocess
import json
import re
import os
import urllib
from datetime import timedelta
from ratelimit import limits, sleep_and_retry
from fuzzywuzzy import fuzz
from fuzzywuzzy import process
from scholarly import scholarly

class RefExtract:
    """
curl -X 'POST' \
  'https://ref.scholarcy.com/api/references/extract' \
  -H 'accept: application/json' \
  -H 'Authorization: Bearer ' \
  -H 'Content-Type: multipart/form-data' \
  -F 'file=@Angel et al. - 2016 - Defending against Malicious Peripherals with Cinch.pdf;type=application/pdf' \
  -F 'document_type=full_paper' \
  -F 'resolve_references=true' \
  -F 'reference_style=ensemble' \
  -F 'engine=v1'
  """

    
    P_REFS_TEST = re.compile("(\[(.*?)\])")
    EXTREF_DIR = "/home/file/proj/zotcit/extref/"

    CURL_CMD = [
        "curl", "-X", "'POST'",
        'https://ref.scholarcy.com/api/references/extract',
        "-H", "'accept: application/json'",
        "-H", "'Authorization: Bearer '",
        "-H", "'Content-Type: multipart/form-data'",
        #"-F", "'file=@Angel et al. - 2016 - Defending against Malicious Peripherals with Cinch.pdf;type=application/pdf'",
        "-F", "'document_type=full_paper'",
        "-F", "'resolve_references=true'",
        "-F", "'reference_style=ensemble'",
        "-F", "'engine=v1'",
        ]

    def __init__(self, sm):
        self.sm = sm

    def __getAllTitles(self, refs, tistr="TI  - "):
        ris = refs["ris"]
        all_titles = set()
        for l in ris.splitlines():
            if l.startswith(tistr):
                all_titles.add(l[len(tistr):])
        return all_titles

    @sleep_and_retry
    @limits(calls=1, period=timedelta(seconds=30).total_seconds())
    def __findTitleScholar(self, slink):
        if len(slink) <= len("msiteDetail2pcDetail 2017"):
            return "?"
        slink = slink.removeprefix("https://scholar.google.co.uk/scholar?q=")
        slink = urllib.parse.unquote(slink)
        logging.info("Searching Google Scholar '%s'" % slink)
        search_query = scholarly.search_pubs(slink)
        try:
            res = next(search_query)
            scholarly.pprint(res)
            title  = res['bib']['title']
            return title
        except StopIteration:
            pass
        except scholarly._navigator.MaxTriesExceededException:
            pass
        return "?"
    
    def __containsTitle(self, t0, t1):
        #logging.info("Compare '%s' / '%s'" % (t0, t1))
        t0 = t0.lower()
        t0 = t0.replace(" ", "")
        t1 = t1.lower()
        t1 = t1.replace(" ", "")
        #logging.info("Compare '%s' / '%s'" % (t0, t1))
        return t0 in t1

    def __findTitleSemanticScholar(self, title):
        #idx2 = self.P_REFS_TEST.findall(title)
        #if idx2 and len(idx2) > 1:
        #    title = title.removeprefix(idx2[0][0])
        logging.info("Searching Semantic Scholar '%s'" % title)
        sm_data = self.sm.searchTitle(title)
        logging.info(json.dumps(sm_data, sort_keys=True, indent=2))
        try:
            for sm_entry in sm_data["data"]:
                if self.__containsTitle(title, sm_entry["title"]):
                    return sm_entry["data"]["title"]
        except:
            pass
        return "?"

    def __findTilte(self, ref_title, all_titles):
        best_r = 0
        best_title = "?"
        for title in all_titles:
            if len(title) < 8:
                continue
            if not self.__containsTitle(title, ref_title):
                continue
            r = fuzz.ratio(title.lower(), ref_title.lower())
            if r == 100:
                best_r = r
                best_title = title
                logging.info("Fuzzy matched title score %d '%s' / '%s'" % (best_r, ref_title, best_title))
                return title
            if r > best_r:
                best_r = r
                best_title = title
        if best_r > 10:
            logging.info("Fuzzy matched title score %d '%s' / '%s'" % (best_r, ref_title, best_title))
        else:
            logging.info("Fuzzy matched title score failed: %d '%s' / '%s'" % (best_r, ref_title, best_title))
            return "?"
        return best_title

    def __parseRefs(self, refs):
        all_titles = self.__getAllTitles(refs)
        last_idx = len(refs["reference_links"])
        titles = {}
        links = {}
        paperIds = {}
        for rl in refs["reference_links"]:
            logging.info(rl["entry"])
            try:
                #buggy api
                idx = rl["id"]
                idx2 = self.P_REFS_TEST.findall(rl['entry'])
                #logging.info(idx2)
                if len(idx2) > 0 and idx != idx2[0]:
                    #logging.info("Replacing ref id %s -> %s" % (idx, idx2[0]))
                    rl["entry"] = rl["entry"].removeprefix(idx2[0][0])
                    rl["entry"] = rl["entry"].removeprefix(" ")
                    idx = idx2[0][1]
                    idx = idx.removesuffix(" ")
                    idx = idx.removeprefix(" ")
                    rl["id"] = idx
                else:
                    logging.info("Broken ref (no id) %s" % json.dumps(rl, sort_keys=True, indent=2))
                    continue
                #if len(rl["entry"]) < 16:
                #    logging.info("Broken ref (too short) %s" % json.dumps(rl, sort_keys=True, indent=2))
                #    continue
            except:
                logging.info("Broken ref (exception) %s" % json.dumps(rl, sort_keys=True, indent=2))
                continue
            logging.debug("Get ref for index %s" % idx)
            if "scholar_url" in rl.keys():
                links[idx] = rl["scholar_url"]
            elif "url" in rl.keys():
                links[idx] = rl["url"]
            stitle = "?"
            if 'scholar_url' in rl.keys():
                stitle = self.__findTilte(rl["entry"], all_titles)
            #if stitle == "?" and 'scholar_url' in rl.keys():
            #    stitle = self.__findTitleSemanticScholar(rl["entry"])
            #if stitle == "?" and 'scholar_url' in rl.keys():
            #    stitle = self.__findTitleScholar(rl["scholar_url"])

            titles[idx] = stitle
        return {
            "titles": titles, 
            "links": links,
            "paperIds": paperIds,
        }

    @sleep_and_retry
    @limits(calls=1, period=timedelta(seconds=10).total_seconds())
    def __getRefs(self, pdfpath):
        this_curl_cmd = self.CURL_CMD + ['-F'] + ["'file=@\"%s\";type=application/pdf'" % pdfpath]
        logging.debug("Executing %s" % " ".join(this_curl_cmd))
        p = subprocess.Popen(" ".join(this_curl_cmd), shell=True, stdout=subprocess.PIPE)
        pout, _ = p.communicate()
        return pout

    def extractRefs(self, pdfpath, paperId):
        #this_curl_cmd = RefExtract.CURL_CMD + ['-F'] + ["'file=@\"%s\";type=application/pdf'" % pdfpath]
        #logging.debug("Executing %s" % " ".join(this_curl_cmd))
        #p = subprocess.Popen(" ".join(this_curl_cmd), shell=True, stdout=subprocess.PIPE)
        #pout, _ = p.communicate()
        refpath = os.path.join(RefExtract.EXTREF_DIR, paperId)
        if os.path.exists(refpath):
            refs = json.loads(open(refpath, "r").read())
        else:
            pout = self.__getRefs(pdfpath)
            try:
                refs = json.loads(pout.decode('utf-8'))
            except Exception as e:
                logging.error("Could not extract references")
                return {}, {}
            open(refpath, "w").write(json.dumps(refs, sort_keys=True, indent=2))
        logging.debug("Parse references for '%s'" % pdfpath)
        #logging.info(refs)
        return self.__parseRefs(refs), refs
