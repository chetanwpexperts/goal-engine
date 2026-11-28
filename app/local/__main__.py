"""Legacy command entry point for DBaba."""
from app.products.dbaba.local.__main__ import main, _env

if __name__ == "__main__":
    raise SystemExit(main())
