# Acme knowledge
#
# Each "## Title" starts one memory item. Everything under it up to the next
# "## " is the paragraph the assistant will answer from.
#
# Optional lines right under the title:
#   category: FACT | SOP | DECISION   (default FACT)
#   area: finance | hr | salary | exec | legal   (restricts who can see it)
#
# Optional test lines anywhere in the section (not stored in memory):
#   Q: a question to ask the assistant
#   Expect: text the answer must contain (repeat Expect: for several)
#
# Lines starting with "#" are comments.

## Dallas warehouse returns
category: SOP

Returns at the Dallas warehouse are accepted Monday to Thursday between 8am and 2pm.
Every returned pallet is inspected by the receiving lead within 24 hours, photographed,
and logged in the returns sheet before restocking. Damaged pallets go to bay 14 and
the carrier claim must be filed within 48 hours.

Q: Which bay do damaged return pallets go to at the Dallas warehouse?
Expect: 14

Q: Within how many hours must the carrier claim be filed for a damaged return?
Expect: 48

## Holiday shipping cutoff
category: FACT

For the 2026 holiday season, the last day to ship standard ground orders is
December 15. Expedited orders must be placed by December 20, and the warehouse
is closed from December 24 to December 27.

Q: What is the last day to ship standard ground orders this holiday season?
Expect: December 15

Q: When is the warehouse closed over the holidays?
Expect: December 24
Expect: December 27
