import logging
import subprocess
import json
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

    def __getParsedRefs(refs):
        pr = []
        for l in refs["references"]:
            if not l.startswith("["):
                logging.warn("Skipping reference line %s" % l.rstrip())
                pr.append(0)
                continue
            r = l.split()[0]
            #logging.info(l.rstrip())
            try:
                pr.append(int(r[1:-1]))
            except Exception as e:
                logging.error("Could not parse reference: %s" % l.rstrip())
        return pr

    def __parseRis(refs, tistr="TI  - "):
        titles = []
        title = "?"
        pr = RefExtract.__getParsedRefs(refs)
        ris = refs['ris']
        pr_cntr = 0
        for l in ris.splitlines():
            next_pr = pr[pr_cntr] - 1
            if l.startswith(tistr):
                title = l[len(tistr):]
            if l == "":
                # buggy api
                if next_pr == -1:
                    pr_cntr += 1
                    continue
                if len(titles) != next_pr:
                    logging.info("missed reference")
                    titles.append("??")
                titles.append(title)
                pr_cntr += 1
                title = "?"
        for i, title in enumerate(titles):
            logging.info("%d: %s" % (i, title))
        
        links = [None] * len(titles)
        for rl in refs["reference_links"]:
            try:
                #buggy api
                idx = int(rl["id"]) - 1
            except:
                continue
            if "scholar_url" in rl.keys():
                links[idx] = rl["scholar_url"]
            elif "url" in rl.keys():
                links[idx] = rl["url"]
        return {
            "titles": titles, 
            "links": links,
        }

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
        titles = [None] * (last_idx+128)
        links = [None] * (last_idx+128)
        for rl in refs["reference_links"]:
            try:
                #buggy api
                idx = int(rl["id"]) - 1
            except:
                logging.info("Broken ref %s" % json.dumps(rl, sort_keys=True, indent=2))
                continue
            logging.info("Get ref for index %d" % idx)
            if "scholar_url" in rl.keys():
                links[idx] = rl["scholar_url"]
            elif "url" in rl.keys():
                links[idx] = rl["url"]
            titles[idx] = RefExtract.__findTilte(rl["entry"], all_titles)
        return {
            "titles": titles, 
            "links": links,
        }


    def extractRefs(pdfpath):
        this_curl_cmd = RefExtract.CURL_CMD + ['-F'] + ["'file=@\"%s\";type=application/pdf'" % pdfpath]
        logging.info("Executing %s" % " ".join(this_curl_cmd))
        p = subprocess.Popen(" ".join(this_curl_cmd), shell=True, stdout=subprocess.PIPE)
        pout, _ = p.communicate()
        try:
            refs = json.loads(pout.decode('utf-8'))
        except Exception as e:
            logging.error("Could not extract references")
            return {}, {}
        logging.info("Parse references for '%s'" % pdfpath)
        #logging.info(refs)
        return RefExtract.__parseRefs(refs), refs
