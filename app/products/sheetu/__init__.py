"""Sheetu product."""
from app.products.shared import bridge
from app.products.sheetu.office import meaning

bridge.provide("sheetu.reading", meaning.Reading)
bridge.provide("sheetu.lexicon", meaning.lexicon)
bridge.provide("sheetu.satisfied", meaning.satisfied)
