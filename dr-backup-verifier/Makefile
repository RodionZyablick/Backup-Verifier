PY ?= python3
CFG ?= config.toml

test:      ## юнит-тесты (Docker не нужен)
	$(PY) -m unittest discover -s tests -t . -v
run:       ## бэкап + проверка
	$(PY) -m verifier -c $(CFG) run
verify:    ## проверить последний бэкап
	$(PY) -m verifier -c $(CFG) verify
list:
	$(PY) -m verifier -c $(CFG) list
cleanup:   ## удалить осиротевшие тестовые контейнеры/сети
	$(PY) -m verifier cleanup
demo:      ## полный демо-сценарий (нужен Docker)
	./scripts/demo.sh
.PHONY: test run verify list cleanup demo
