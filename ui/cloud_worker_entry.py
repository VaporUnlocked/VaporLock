"""Frozen Cloud entry: only two compiled dispatches, same existing GO protocols."""
import sys


def main():
    from product_build import CLOUD_ROLE, EDITION
    if CLOUD_ROLE != 'cloud-worker' or EDITION not in ('free', 'premium'):
        return 2
    if sys.argv[1:] == ['--annotation-gated']:
        sys.argv[1:] = ['--gated']
        from cloud_annotation_worker import main as run
    elif sys.argv[1:] == ['--gated']:
        from cloud_worker import main as run
    else:
        return 2
    return run()


if __name__ == '__main__':
    raise SystemExit(main())
