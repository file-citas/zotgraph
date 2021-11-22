from flask import Flask, render_template, request, redirect, url_for
app = Flask(__name__)
app.config.from_pyfile('config.py')
import logging
FORMAT = "[%(filename)s:%(lineno)s - %(funcName)20s() ] %(message)s"
logging.basicConfig(format=FORMAT, level=logging.INFO)

import os
import json

from zotgraph import ZotGraph

      
ID_FILTER_FN="paperIds.filter"
CONF_FILTER_FN="filter.conf"

projects = {}

def getProjectList():
    pl = []
    for f in os.listdir(app.config["PROJ_DIR"]):
        pl.append(f)
    return pl

@app.route("/")
def home():
    projectlist = getProjectList()
    return render_template(
        "project.html",
        projectlist=projectlist
    )


@app.route("/load_project")
def load_project():
    global projects
    pname = request.args.get("pname")
    logging.info("loading project '%s'" % pname)
    if pname == "": 
      return "Invalid Project Name"
    ppath = os.path.join(app.config['PROJ_DIR'], pname)
    if not os.path.exists(ppath):
      return "Project %s does not exists" % pname

    fconf_fn = os.path.join(ppath, CONF_FILTER_FN)
    with open(fconf_fn, "r") as fd: 
        filters = json.loads(fd.read())
    id_filter_fn = os.path.join(ppath, ID_FILTER_FN)
    graph_path = os.path.join(ppath, "graph.pkl")
    projects[pname] = ZotGraph(
        graph_path,
        filters, 
        app.config['HTML_DIR'], 
        app.config['N_CACHE'],
        id_filter_fn, 
        app.config['LCSV'], 
        app.config['LIBRARY_ID'], 
        app.config["LIBRARY_TYPE"], 
        app.config['API_KEY'])
    return redirect(url_for('zotcit', pname=pname))

@app.route("/create_project")
def create_project():
    global projects
    pname = request.args.get("pname")
    fyear = request.args.get("year")
    fcit = request.args.get("ncit")
    logging.info("creating project '%s'" % pname)
    if pname == "": 
      return "Invalid Project Name"
    ppath = os.path.join(app.config['PROJ_DIR'], pname)
    if os.path.exists(ppath):
      return "Project %s already exists" % pname

    os.mkdir(ppath)
    filters = { 
         'year': int(fyear),
         'cit': int(fcit),
         'dist': 100,
         }   
    fconf_fn = os.path.join(ppath, CONF_FILTER_FN)
    with open(fconf_fn, "w") as fd: 
        fd.write(json.dumps(filters, sort_keys=True, indent=2))

    graph_path = os.path.join(ppath, "graph.pkl")
    id_filter_fn = os.path.join(ppath, ID_FILTER_FN)
    projects[pname] = ZotGraph(
        graph_path,
        filters, 
        app.config['HTML_DIR'], 
        app.config['N_CACHE'],
        id_filter_fn, 
        app.config['LCSV'], 
        app.config['LIBRARY_ID'], 
        app.config["LIBRARY_TYPE"], 
        app.config['API_KEY'])
    return redirect(url_for('zotcit', pname=pname))
 

@app.route('/zotcit')
def zotcit():
    pname = request.args.get("pname")
    logging.info("zotcit project %s" % pname)
    nodes = []
    edges = []
    nodes, edges = projects[pname].getGraph()
    paperInfo = projects[pname].getPaperInfo()
    return render_template(
        "papers.html",
        nodes=nodes,
        edges=edges,
        paperinfo=paperInfo,
        pname=pname
    )

@app.route('/addpaper')
def addpaper():
    pname = request.args.get("pname")
    paperId = request.args.get("paperid")
    logging.info("Add PaperId %s" % paperId)
    new_edges = []
    new_nodes = []
    if paperId is not None:
        new_nodes, new_edges, new_paperinfo = projects[pname].addPaperId(paperId)
        projects[pname].saveGraph()
    res = {
        'new_nodes': new_nodes,
        'new_edges': new_edges,
        'new_paperinfo': new_paperinfo,
    }
    return res

@app.route('/getcits')
def getcits():
    pname = request.args.get("pname")
    paperId = request.args.get("paperid")
    logging.info("Get citations for PaperId %s" % paperId)
    res = {}
    new_edges = []
    new_nodes = []
    if paperId is not None:
        new_nodes, new_edges, new_paperinfo = projects[pname].addLinks(paperId, onlyCit=True)
        projects[pname].saveGraph()
    res = {
        'new_nodes': new_nodes,
        'new_edges': new_edges,
        'new_paperinfo': new_paperinfo,
    }
    return res

@app.route('/getrefs')
def getrefs():
    pname = request.args.get("pname")
    paperId = request.args.get("paperid")
    logging.info("Get references for PaperId %s" % paperId)
    res = {}
    new_edges = []
    new_nodes = []
    if paperId is not None:
        new_nodes, new_edges, new_paperinfo = projects[pname].addLinks(paperId, onlyRef=True)
        projects[pname].saveGraph()
    res = {
        'new_nodes': new_nodes,
        'new_edges': new_edges,
        'new_paperinfo': new_paperinfo,
    }
    return res

@app.route('/filter')
def filter():
    pname = request.args.get("pname")
    paperId = request.args.get("paperid")
    logging.info("Filter PaperId %s" % paperId)
    if paperId is not None:
        projects[pname].removePaperId(paperId)
        projects[pname].saveGraph()
    return {"status": "OK"}

@app.route('/setcolor')
def setcolor():
    pname = request.args.get("pname")
    color = request.args.get("color")
    if projects[pname].setColoring(color):
        new_nodes, _ = projects[pname].getGraph()
        return {
            'new_nodes': new_nodes,
        }
    return {
            'new_nodes': [],
        }
    #return redirect(url_for('zotcit', pname=pname))