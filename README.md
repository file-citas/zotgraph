# ZotGraph
ZotGraph correlates information from your Zotero Database with Semantic Scholar.

# Dependencies:
* [PyZotero](https://pyzotero.readthedocs.io/en/latest/)
* [Flask](https://pypi.org/project/Flask/)

# Setup
```
git clone https://github.com/file-citas/zotgraph.git
cd zotgraph
mkdir projects
mkdir htmls
mkdir ncache
```
Edit `zotgraph/zotconfig.py`
 * PROJ_DIR: zotgrap/projects
 * HTML_DIR: zotgrap/htmls
 * N_CACHE: zotgrap/ncache
 * API_KEY: Your personal library ID is available [here](https://www.zotero.org/settings/keys), in the section Your userID for use in API calls. You have to sign in first
 * LCSV: The path you your exported Zotero library. From Zotero click file->Export Library and select CSV with 'export Notes'.
 
# Run
```
cd zotgraph
export FLASK_APP=app.py
flask run
```

This will spawn a webserver listining on [localhost:5000](http:http://localhost:5000/).
