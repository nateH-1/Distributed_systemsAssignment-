# Distributed mailbox - node-1 front end

This folder contains the front-end portion of CS 415 Assignment 2. It uses
only the Python 3.13 standard library.

## Start node-1

Run from this directory:

```bash
export MAIL_SITE_NAME=site-a
export MAIL_SITES='[{"name":"site-a","host":"10.0.0.11"},{"name":"site-b","host":"10.0.0.12"},{"name":"site-c","host":"10.0.0.13"}]'
export MAIL_STORAGE_NODES='node-2,node-3'
python3 frontend.py
```

Replace the example site names and hosts with the group network addresses
provided by the instructor. Every group member must use exactly the same,
same-order `MAIL_SITES` value. Change only `MAIL_SITE_NAME` at each site.

## Storage-team integration contract

The storage service listens on port 9000 by default and must expose the
internal HTTP/JSON endpoints documented at the top of `storage_client.py`.
Node-1 makes no local mailbox files; it calls an available storage node.

## Quick checks

```bash
curl http://localhost:8000/status
curl -X POST http://localhost:8000/mail -H 'Content-Type: application/json' \
  -d '{"from":"bob","to":"alice","subject":"hello","body":"hi"}'
curl http://localhost:8000/mailbox/alice
```

Run unit tests:

```bash
python3 -m unittest discover -s tests -v
```
