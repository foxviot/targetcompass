# Dataset Feasibility Rules

Feasibility review must consider:

- species
- tissue
- disease or phenotype labels
- assay type
- sample size or cell count
- metadata availability
- matrix availability
- raw or processed data availability
- batch information
- clinical phenotype fields
- disease-control contrast
- cell-type resolution
- independent validation possibility

Recommendation classes:

- `primary`: directly supports a core part of the question
- `fallback`: supports a partial or indirect part of the question
- `reject`: missing a required field or mismatched to scope

Agent 6 must not use datasets labeled `reject`.
