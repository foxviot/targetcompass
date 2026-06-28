# Ontology Sources

The mock system resolves biological scope conservatively using canonical term families rather than live ontology APIs.

Preferred term families:

- disease and phenotype terms
- tissue and organ terms
- cell type labels
- molecular localization labels
- pathway and inflammatory program labels

Resolver policy:

- preserve ambiguity when multiple branches remain plausible
- keep search terms separate from final claims
- record excluded expansions explicitly
- never invent ontology identifiers
