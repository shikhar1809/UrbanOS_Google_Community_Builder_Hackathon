import sys

with open("main.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update Message
code = code.replace(
    '    status = Column(String, default="Open")',
    '    status = Column(String, default="Open")\n    constituency_zone = Column(String, nullable=True)\n    estimated_budget = Column(Integer, nullable=True)'
)

# 2. Update TriageResult
code = code.replace(
    '    original_language: str = Field(description="The language the user originally submitted their request in (e.g., Hindi, English, Spanish).")',
    '    original_language: str = Field(description="The language the user originally submitted their request in (e.g., Hindi, English, Spanish).")\n    constituency_zone: str = Field(description="Must be one of: North, South, East, West, Central. Infer this constituency zone from the location context. Default to Central if unknown.")\n    estimated_budget: int = Field(description="An integer representing the estimated cost in INR based on the project scale (e.g., 500000 for small, 50000000 for large capital works).")'
)

# 3. Update init vars
code = code.replace(
    '        summary = None\n        original_language = None',
    '        summary = None\n        original_language = None\n        constituency_zone = None\n        estimated_budget = None'
)

# 4. Update prompt
code = code.replace(
    'prompt = f"Analyze this community development proposal from a Smart City WhatsApp tip-line. Extract the structured triage data, including what language they originally used.\\n\\nProposal Text: {description}"',
    'prompt = f"Analyze this community development proposal from a Smart City WhatsApp tip-line. Extract the structured triage data, including what language they originally used, an estimated budget in INR, and assign it to a constituency zone (North, South, East, West, Central).\\n\\nProposal Text: {description}"'
)

# 5. Update parsing
code = code.replace(
    '                summary = triage.summary',
    '                summary = triage.summary\n                constituency_zone = triage.constituency_zone\n                estimated_budget = triage.estimated_budget'
)

# 6. Update new_message creation
code = code.replace(
    '            original_language=original_language,\n            reference_id=ref_id,\n            status="Open"',
    '            original_language=original_language,\n            constituency_zone=constituency_zone,\n            estimated_budget=estimated_budget,\n            reference_id=ref_id,\n            status="Open"'
)

with open("main.py", "w", encoding="utf-8") as f:
    f.write(code)
