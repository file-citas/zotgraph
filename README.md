# ZotGraph
ZotGraph correlates information from your Zotero Database with Semantic Scholar.

# Dependencies:
* [PyZotero](https://pyzotero.readthedocs.io/en/latest/)
* [Flask](https://pypi.org/project/Flask/)
* [EasyUi](https://www.jeasyui.com/index.php)

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

Download [EasyUi](https://www.jeasyui.com/download/list.php) and extract it into `zotgraph/static/easyui/`
 
# Run
```
cd zotgraph
export FLASK_APP=app.py
flask run
```

This will spawn a webserver listining on [localhost:5000](http:http://localhost:5000/).

# Usage

## Start Screen
Create a new project or select an existing project.


![start screen](/images/start_screen.png)

## Project Screen
Add a new paper via semantic scholar id

![add paper](/images/addpaper.png)

After selecting a paper from the graph display or the table, you can get citations and references for the selected paper. They will be added to the Graph.

![citations and references](/images/getcr.png)

If an added paper (either via 'add paper' or citations/references) is not interesting, you can filter it by selecting the paper and clicking 'filter'.

![filter](/images/filter.png)

The default color scheme is based on your zotero collections, other supported color schemes are author, citation count and year.

![author color](/images/color.png)

ZotGraph will parse your Zotero Annotations and add them to the display.

![annotations](/images/annotations.png)

Also, if you have any references in your annotations, ZotGraph will parse them and display them under the referenced paper.

![mentions](/images/mentions.png)
