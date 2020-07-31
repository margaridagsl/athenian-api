import base64
import bz2
import pickle

import pandas as pd


def main():
    """Usage: `echo 'string after UUID'| python3 show_sql.py`."""
    pd.set_option("display.max_rows", None)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.max_colwidth", None)
    data = input()
    query, args = pickle.loads(bz2.decompress(base64.b64decode(data)))
    print(query, flush=True)
    print("=" * 80)
    for i, arg in enumerate(args, start=1):
        print("$%d = %s" % (i, arg))
        print("-" * 80)


if __name__ == "__main__":
    exit(main())
