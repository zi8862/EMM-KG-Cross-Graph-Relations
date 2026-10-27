# EMM-KG Cross-Graph Relation Construction Methods

This repository contains selected Python implementations used to construct three types of cross-graph relations in EMM-KG: `isSameAs`, `belongsTo`, and `contains`.

The code is organized by relation type. Each Python file implements an individual method or processing step. This repository is a partial code release; it is **not** a complete end-to-end pipeline for rebuilding the entire EMM-KG knowledge graph from the original source data.

## Repository structure

```text
EMM-KG-Cross-Graph-Relations/
├── README.md
├── requirements.txt
├── .env.example
├── .gitignore
├── isSameAs/
│   ├── chem\_formula\_match.py
│   ├── matscibert\_match.py
│   ├── tfidf\_match.py
│   └── word2vec\_match.py
├── belongsTo/
│   ├── belongsTo\_layer1.py
│   ├── belongsTo\_layer2.py
│   ├── belongsTo\_layer3.py
│   └── belongsTo\_layer4.py
└── contains/
    ├── contains\_element.py
    ├── contains\_simple.py
    ├── deepseak.py
    ├── llm\_fusion.py
    ├── llm\_fusion\_qwen.py
    └── qwen.py
```

## Included methods

### `isSameAs/`

This directory contains alternative scripts for identifying candidate entity-equivalence links:

* `chem\_formula\_match.py` : chemical-formula-based matching variants.
* `tfidf\_match.py`: TF-IDF-based matching.
* `word2vec\_match.py` : Word2Vec-based matching variants.
* `matscibert\_match.py` : MatSciBERT-based matching variants.

These scripts are provided as separate implementations.

### `belongsTo/`

This directory contains four layer-specific scripts for constructing `belongsTo` relations: `belongsTo\_layer1.py`, `belongsTo\_layer2.py`, `belongsTo\_layer3.py`, and `belongsTo\_layer4.py`. Some steps may depend on the graph state or relationships produced by earlier steps, so they should not be assumed to be independently interchangeable.

### `contains/`

This directory contains scripts for constructing `contains` relations using element-based, rule-based, and LLM-assisted approaches:

* `contains\_element.py`
* `contains\_simple.py`
* `deepseak.py`
* `llm\_fusion.py`
* `llm\_fusion\_qwen.py`
* `qwen.py`

LLM-assisted scripts require access to the relevant provider API and may incur usage charges. The results can depend on the provider, model version, prompt, and API behavior.

## Software requirements

The top-level `requirements.txt` lists Python packages referenced by the included scripts. The scripts were not packaged with one verified, pinned environment, and not every dependency is required by every method. Install the packages needed for the specific script you intend to use, and check compatibility with the Python version and model libraries in your environment.

A running Neo4j instance is required for scripts that connect to Neo4j. Depending on the selected method, additional requirements may include locally available model checkpoints or embeddings, input files, graph properties, and external LLM API access. Model weights, source-graph dumps, embeddings, and large intermediate datasets are not included in this repository.

## Configuration and credentials

The `.env.example` file documents common configuration variables used by the scripts. It contains no real credentials. The scripts may use different subsets of these variables, and some connection settings may remain defined as constants in individual files.

For scripts that use these variables, configure the relevant values in your local environment:

* `NEO4J\_URI`
* `NEO4J\_USER`
* `NEO4J\_PASSWORD`
* `NEO4J\_DATABASE`
* `DEEPSEEK\_API\_KEY`
* `DASHSCOPE\_API\_KEY`

## Running the scripts

There is no single command that executes the entire repository. Each script is a separate method or workflow and may have its own input format, configuration, and execution assumptions. Before running a script, inspect its code and confirm that:

1. the required input data and graph properties are available;
2. the required Python packages and model assets are installed;
3. database connection settings and API credentials are configured where needed; and
4. the target database is backed up or a disposable test database is being used.

Some scripts write relationships to Neo4j or modify graph data. Test them on a non-production database before using them with an existing knowledge graph.

## Data availability

This repository contains selected relation-construction methods and does not include the full graph dump or all files required to reproduce the entire graph from scratch.

## Scope and limitations

* The release focuses on selected methods for constructing `isSameAs`, `belongsTo`, and `contains` cross-graph relations.
* The repository does not include the complete end-to-end graph-construction pipeline, source-graph dumps, all preprocessing assets, model checkpoints, embedding arrays, or intermediate result tables.
* Reproduction may depend on the source graph versions and schema, input data, Python package compatibility, model assets, Neo4j configuration, and external API/model versions.

## Citation

If you use these scripts in research, please cite the EMM-KG study and the archived version of this code when a versioned archive is available.

