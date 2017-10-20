build:
	virtualenv .
	bin/python bootstrap.py -c base.cfg --setuptools-version=18.2
	bin/buildout -c buildout-plone-4.3.cfg

build5:
	virtualenv .
	bin/python bootstrap.py -c base.cfg  --setuptools-version=18.2
	bin/buildout -c buildout-plone-5.0.cfg

release:
	mkrelease -p -d pypi

docs:
	cd docs; make html

upload-docs:
	python setup.py upload_docs --upload-dir docs/build/html

test:
	bin/test zopyx.plone.persistentlogger

