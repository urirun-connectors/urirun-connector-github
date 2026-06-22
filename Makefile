.PHONY: help bindings test
help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-9s %s\n",$$1,$$2}'
bindings: ## Print urirun bindings
	urirun-connector-github bindings
test: ## Install editable + pytest
	pip install -e . && python3 -m pytest -q
