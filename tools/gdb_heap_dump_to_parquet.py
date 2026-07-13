#!/usr/bin/env python3
#**************************************************************************
#*                                                                        *
#*                                 OCaml                                  *
#*                                                                        *
#*   All rights reserved.  This file is distributed under the terms of   *
#*   the GNU Lesser General Public License version 2.1, with the         *
#*   special exception on linking described in the file LICENSE.         *
#*                                                                        *
#**************************************************************************

"""Convert a `tools/gdb.py` "ocaml dump-heap" JSONL dump into parquet,
doing domain/thread dedup and an unswept-pools invariant check along the
way.

Usage:
    tools/gdb_heap_dump_to_parquet.py <dump-dir> <output-dir>

Reads <dump-dir>/{domains,threads,pools,large,global,blocks}.jsonl
(blocks.jsonl is optional, only present for `ocaml dump-heap --full`), and
writes one parquet file per table into <output-dir>, plus a
domain_thread_map.parquet built by matching each `threads` row's
`caml_state` pointer against the `domains` rows.

Unlike `tools/gdb.py` (loaded into every GDB session that sources it),
this script runs under a normal python3 and is allowed a hard dependency
on pandas/pyarrow.
"""

import argparse
import json
import os
import sys


TABLES = ['domains', 'threads', 'pools', 'large', 'global', 'blocks']


def load_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def load_dump(dump_dir):
    "Read every `*.jsonl` table present in `dump_dir` into plain lists of dicts."
    tables = {}
    for name in TABLES:
        path = os.path.join(dump_dir, f'{name}.jsonl')
        if os.path.exists(path):
            tables[name] = load_jsonl(path)
    return tables


def build_domain_thread_map(tables):
    """Match `threads` rows against `domains` rows by `caml_state` pointer
    identity (NOT by thread/pid — pthread_t is an opaque handle on
    glibc/NPTL and doesn't correspond to the kernel TID GDB reports, so
    it can't be used to match a live thread to the domain it's running).

    Returns `(rows, unmatched_threads, unmatched_domains)` where `rows` is
    one dict per (thread, domain) pair sharing a `caml_state` address.
    """
    domains = tables.get('domains', [])
    threads = tables.get('threads', [])
    by_state = {}
    for d in domains:
        by_state.setdefault(d['caml_state'], []).append(d)

    rows = []
    unmatched_threads = []
    matched_states = set()
    for t in threads:
        state = t.get('caml_state')
        matches = by_state.get(state) if state is not None else None
        if not matches:
            unmatched_threads.append(t)
            continue
        matched_states.add(state)
        for d in matches:
            rows.append({
                'caml_state': state,
                'thread_num': t['thread_num'],
                'ptid': t['ptid'],
                'domain_index': d['index'],
                'domain_id': d['id'],
                'domain_unique_id': d['unique_id'],
            })

    unmatched_domains = [d for d in domains
                        if d['caml_state'] not in matched_states]
    return rows, unmatched_threads, unmatched_domains


def check_sweep_invariant(tables):
    """Print a warning for any domain whose unswept pool/large lists
    weren't empty (see runtime/shared_heap.c: caml_sweep) — expected to
    hold if the dump was taken at a major-GC-cycle boundary."""
    domains = tables.get('domains', [])
    if not domains:
        return
    bad = [d for d in domains if (d.get('unswept_pool_count') or 0) != 0]
    if bad:
        print(f"WARNING: sweep invariant violated for {len(bad)} "
              "domain(s):")
        for d in bad:
            print(f"  domain {d['index']}: "
                  f"unswept_pool_count={d['unswept_pool_count']}")
    else:
        print(f"sweep invariant holds: all {len(domains)} domain(s) have "
              "0 unswept pools")


def write_parquet(records, path, pd):
    pd.DataFrame.from_records(records).to_parquet(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('dump_dir',
                        help='directory produced by `ocaml dump-heap`')
    parser.add_argument('output_dir',
                        help='directory to write parquet files into')
    args = parser.parse_args(argv)

    tables = load_dump(args.dump_dir)
    if not tables:
        sys.exit(f"no *.jsonl files found in {args.dump_dir}")

    check_sweep_invariant(tables)

    rows, unmatched_threads, unmatched_domains = build_domain_thread_map(
        tables)
    if unmatched_threads:
        print(f"WARNING: {len(unmatched_threads)} thread(s) with no "
              "matching domain (caml_state not found in `all_domains`)")
    if unmatched_domains:
        print(f"WARNING: {len(unmatched_domains)} domain(s) with no "
              "matching live thread (parked/unscheduled domain?)")

    try:
        import pandas as pd
    except ImportError:
        sys.exit("This script requires pandas (and a parquet engine such "
                  "as pyarrow) to write parquet output — the "
                  "`ocaml dump-heap` GDB command itself has no such "
                  "dependency. Try: pip install pandas pyarrow")

    os.makedirs(args.output_dir, exist_ok=True)
    for name, records in tables.items():
        out_path = os.path.join(args.output_dir, f'{name}.parquet')
        write_parquet(records, out_path, pd)
        print(f"wrote {out_path} ({len(records)} rows)")

    if rows:
        out_path = os.path.join(args.output_dir,
                                'domain_thread_map.parquet')
        write_parquet(rows, out_path, pd)
        print(f"wrote {out_path} ({len(rows)} rows)")


if __name__ == '__main__':
    main()
