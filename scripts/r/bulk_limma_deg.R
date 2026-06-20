#!/usr/bin/env Rscript

suppressPackageStartupMessages({
  library(limma)
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 6) {
  stop("Usage: bulk_limma_deg.R <expression_matrix.tsv> <metadata.tsv> <case> <control> <out_dir> <batch_covariates_csv>")
}

expr_path <- args[[1]]
meta_path <- args[[2]]
case_label <- args[[3]]
control_label <- args[[4]]
out_dir <- args[[5]]
batch_covariates <- if (args[[6]] == "") character() else strsplit(args[[6]], ",", fixed = TRUE)[[1]]

dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

expr <- read.delim(expr_path, check.names = FALSE, stringsAsFactors = FALSE)
meta <- read.delim(meta_path, check.names = FALSE, stringsAsFactors = FALSE)
if (!"gene_symbol" %in% colnames(expr)) {
  stop("expression matrix must contain gene_symbol column")
}
if (!all(c("sample_id", "group") %in% colnames(meta))) {
  stop("metadata must contain sample_id and group columns")
}

sample_cols <- setdiff(colnames(expr), "gene_symbol")
if (!setequal(sample_cols, meta$sample_id)) {
  stop("sample IDs do not match between expression matrix and metadata")
}
meta <- meta[match(sample_cols, meta$sample_id), ]
meta$group <- factor(meta$group, levels = c(control_label, case_label))
if (any(is.na(meta$group))) {
  stop("case/control labels are missing in metadata")
}

design_terms <- "~ group"
for (covariate in batch_covariates) {
  if (!covariate %in% colnames(meta)) {
    stop(paste("batch covariate missing in metadata:", covariate))
  }
  meta[[covariate]] <- factor(meta[[covariate]])
  design_terms <- paste(design_terms, "+", covariate)
}
design <- model.matrix(as.formula(design_terms), data = meta)
if (qr(design)$rank < ncol(design)) {
  stop("design matrix is rank deficient")
}

mat <- as.matrix(expr[, sample_cols])
rownames(mat) <- expr$gene_symbol
fit <- lmFit(mat, design)
fit <- eBayes(fit)
coef_name <- grep("^group", colnames(design), value = TRUE)[1]
res <- topTable(fit, coef = coef_name, number = Inf, sort.by = "P")
out <- data.frame(
  gene_symbol = rownames(res),
  logFC = res$logFC,
  p_value = res$P.Value,
  adj_p_value = res$adj.P.Val,
  direction = ifelse(res$logFC > 0, "up", "down"),
  stringsAsFactors = FALSE
)
write.table(out, file.path(out_dir, "deg_results.tsv"), sep = "\t", row.names = FALSE, quote = FALSE)
write.table(
  data.frame(
    metric = c("case_samples", "control_samples", "genes", "design_rank", "design_columns", "batch_covariates"),
    value = c(
      sum(meta$group == case_label),
      sum(meta$group == control_label),
      nrow(out),
      qr(design)$rank,
      ncol(design),
      ifelse(length(batch_covariates) == 0, "none", paste(batch_covariates, collapse = ","))
    )
  ),
  file.path(out_dir, "qc_summary.tsv"),
  sep = "\t",
  row.names = FALSE,
  quote = FALSE
)
