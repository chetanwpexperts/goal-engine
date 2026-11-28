"""DBaba product."""
from app.products.shared import bridge
from app.products.dbaba.dbknow import concepts

bridge.provide("dbaba.concept_match", concepts.match)
