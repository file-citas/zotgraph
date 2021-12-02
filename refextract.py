import logging
import subprocess
import json
from datetime import timedelta
from ratelimit import limits, sleep_and_retry
from fuzzywuzzy import fuzz
from fuzzywuzzy import process

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


    def __getAllTitles(refs, tistr="TI  - "):
        ris = refs["ris"]
        all_titles = set()
        for l in ris.splitlines():
            if l.startswith(tistr):
                all_titles.add(l[len(tistr):])
        return all_titles

    def __findTilte(ref_title, all_titles):
        best_r = 0
        best_title = "?"
        for title in all_titles:
            r = fuzz.partial_ratio(title, ref_title)
            if r == 100:
                return title
            if r > best_r:
                best_r = r
                best_title = title
        return best_title

    def __parseRefs(refs):
        all_titles = RefExtract.__getAllTitles(refs)
        last_idx = len(refs["reference_links"])
        titles = {}
        links = {}
        paperIds = {}
        for rl in refs["reference_links"]:
            try:
                #buggy api
                idx = rl["id"]
            except:
                logging.info("Broken ref %s" % json.dumps(rl, sort_keys=True, indent=2))
                continue
            logging.debug("Get ref for index %s" % idx)
            if "scholar_url" in rl.keys():
                links[idx] = rl["scholar_url"]
            elif "url" in rl.keys():
                links[idx] = rl["url"]
            titles[idx] = RefExtract.__findTilte(rl["entry"], all_titles)
        return {
            "titles": titles, 
            "links": links,
            "paperIds": paperIds,
        }



    @sleep_and_retry
    @limits(calls=1, period=timedelta(seconds=10).total_seconds())
    def __getRefs(pdfpath):
        this_curl_cmd = RefExtract.CURL_CMD + ['-F'] + ["'file=@\"%s\";type=application/pdf'" % pdfpath]
        logging.debug("Executing %s" % " ".join(this_curl_cmd))
        p = subprocess.Popen(" ".join(this_curl_cmd), shell=True, stdout=subprocess.PIPE)
        pout, _ = p.communicate()
        return pout

    def extractRefs(pdfpath):
        #this_curl_cmd = RefExtract.CURL_CMD + ['-F'] + ["'file=@\"%s\";type=application/pdf'" % pdfpath]
        #logging.debug("Executing %s" % " ".join(this_curl_cmd))
        #p = subprocess.Popen(" ".join(this_curl_cmd), shell=True, stdout=subprocess.PIPE)
        #pout, _ = p.communicate()
        pout = __getRefs(pdfpath)
        try:
            refs = json.loads(pout.decode('utf-8'))
        except Exception as e:
            logging.error("Could not extract references")
            return {}, {}
        logging.debug("Parse references for '%s'" % pdfpath)
        #logging.info(refs)
        return RefExtract.__parseRefs(refs), refs
