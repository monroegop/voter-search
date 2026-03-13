# Voter Registry Search

A fast local search tool for large voter CSV files, backed by SQLite.

## Setup (one time)

```bash
pip install flask
```

## Run

```bash
python app.py
```

Then open your browser to: **http://localhost:5000**

## Usage

1. Drop or select your CSV file — any size works
2. Wait for the import (a 1M-row file takes ~10–20 seconds)
3. Search instantly across all fields — partial matches work on every field
4. Click column headers to sort

## CSV Column Auto-Detection

The app auto-maps common header variations:

| Field        | Accepted Header Names                                      |
|-------------|-------------------------------------------------------------|
| Voter ID     | voter id, voterid, voter_id, id, regnum, reg_num           |
| First Name   | first name, firstname, first_name, fname, given name        |
| Last Name    | last name, lastname, last_name, lname, surname              |
| Address      | address, street, street address, addr, res address          |
| Town / City  | town, city, municipality, city/town, muni, residence city   |
| Party        | party, party affiliation, affiliation, enrollment           |

## Notes

- The database (`voters.db`) persists between sessions — no re-upload needed
- Uploading a new file replaces the existing data
- Searches use SQLite indexes for fast LIKE queries even on millions of rows
- For Excel files: File → Save As → CSV, then upload
