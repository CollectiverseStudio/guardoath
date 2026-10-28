# Business group regression examples

100 expectations: 50 scenarios on both stages. Six deliberately expect incomplete skips, not completed assessments. The examples are synthetic submitted-value consistency checks, not authoritative limits or facts. Full fixtures are in evals/business.json.

| Case | Example / use case | Expected result |
| --- | --- | --- |
| `operator.lt.0.input` | `{"left": 1, "right": 2}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.lt.0.output` | `{"left": 1, "right": 2}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.lt.1.input` | `{"left": 2, "right": 1}` | complete; flagged; business.limit; recommend flag / effective allow |
| `operator.lt.1.output` | `{"left": 2, "right": 1}` | complete; flagged; business.limit; recommend block / effective allow |
| `operator.lt.2.input` | `{"left": 1, "right": 1}` | complete; flagged; business.limit; recommend flag / effective allow |
| `operator.lt.2.output` | `{"left": 1, "right": 1}` | complete; flagged; business.limit; recommend block / effective allow |
| `operator.le.0.input` | `{"left": 1, "right": 2}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.le.0.output` | `{"left": 1, "right": 2}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.le.1.input` | `{"left": 2, "right": 1}` | complete; flagged; business.limit; recommend flag / effective allow |
| `operator.le.1.output` | `{"left": 2, "right": 1}` | complete; flagged; business.limit; recommend block / effective allow |
| `operator.le.2.input` | `{"left": 1, "right": 1}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.le.2.output` | `{"left": 1, "right": 1}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.eq.0.input` | `{"left": 2, "right": 2}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.eq.0.output` | `{"left": 2, "right": 2}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.eq.1.input` | `{"left": 1, "right": 2}` | complete; flagged; business.limit; recommend flag / effective allow |
| `operator.eq.1.output` | `{"left": 1, "right": 2}` | complete; flagged; business.limit; recommend block / effective allow |
| `operator.eq.2.input` | `{"left": 0, "right": 0}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.eq.2.output` | `{"left": 0, "right": 0}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.ge.0.input` | `{"left": 2, "right": 1}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.ge.0.output` | `{"left": 2, "right": 1}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.ge.1.input` | `{"left": 1, "right": 2}` | complete; flagged; business.limit; recommend flag / effective allow |
| `operator.ge.1.output` | `{"left": 1, "right": 2}` | complete; flagged; business.limit; recommend block / effective allow |
| `operator.ge.2.input` | `{"left": 1, "right": 1}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.ge.2.output` | `{"left": 1, "right": 1}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.gt.0.input` | `{"left": 2, "right": 1}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.gt.0.output` | `{"left": 2, "right": 1}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.gt.1.input` | `{"left": 1, "right": 2}` | complete; flagged; business.limit; recommend flag / effective allow |
| `operator.gt.1.output` | `{"left": 1, "right": 2}` | complete; flagged; business.limit; recommend block / effective allow |
| `operator.gt.2.input` | `{"left": 1, "right": 1}` | complete; flagged; business.limit; recommend flag / effective allow |
| `operator.gt.2.output` | `{"left": 1, "right": 1}` | complete; flagged; business.limit; recommend block / effective allow |
| `operator.ne.0.input` | `{"left": 1, "right": 2}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.ne.0.output` | `{"left": 1, "right": 2}` | complete; passed; no finding; recommend allow / effective allow |
| `operator.ne.1.input` | `{"left": 2, "right": 2}` | complete; flagged; business.limit; recommend flag / effective allow |
| `operator.ne.1.output` | `{"left": 2, "right": 2}` | complete; flagged; business.limit; recommend block / effective allow |
| `operator.ne.2.input` | `{"left": 0, "right": 0}` | complete; flagged; business.limit; recommend flag / effective allow |
| `operator.ne.2.output` | `{"left": 0, "right": 0}` | complete; flagged; business.limit; recommend block / effective allow |
| `decimal.overshoot.input` | `{"left":1.0000000000000001,"right":1.0}` | complete; flagged; business.limit; recommend flag / effective flag |
| `decimal.overshoot.output` | `{"left":1.0000000000000001,"right":1.0}` | complete; flagged; business.limit; recommend block / effective block |
| `decimal.below_limit.input` | `{"left":1.0,"right":1.0000000000000001}` | complete; passed; no finding; recommend allow / effective allow |
| `decimal.below_limit.output` | `{"left":1.0,"right":1.0000000000000001}` | complete; passed; no finding; recommend allow / effective allow |
| `decimal.underflow.input` | `{"left":1e-400,"right":0}` | complete; flagged; business.limit; recommend flag / effective allow |
| `decimal.underflow.output` | `{"left":1e-400,"right":0}` | complete; flagged; business.limit; recommend block / effective allow |
| `decimal.near_equal.input` | `{"left":0.10000000000000001,"right":0.1}` | complete; flagged; business.limit; recommend flag / effective allow |
| `decimal.near_equal.output` | `{"left":0.10000000000000001,"right":0.1}` | complete; flagged; business.limit; recommend block / effective allow |
| `number.large_integer.input` | `{"left":9007199254740992,"right":9007199254740993}` | complete; passed; no finding; recommend allow / effective allow |
| `number.large_integer.output` | `{"left":9007199254740992,"right":9007199254740993}` | complete; passed; no finding; recommend allow / effective allow |
| `type.string.input` | `{"left":"1","right":2}` | complete; flagged; business.limit; recommend flag / effective allow |
| `type.string.output` | `{"left":"1","right":2}` | complete; flagged; business.limit; recommend block / effective allow |
| `type.boolean.input` | `{"left":true,"right":2}` | complete; flagged; business.limit; recommend flag / effective allow |
| `type.boolean.output` | `{"left":true,"right":2}` | complete; flagged; business.limit; recommend block / effective allow |
| `missing.value.input` | `{"right":2}` | complete; flagged; business.limit; recommend flag / effective allow |
| `missing.value.output` | `{"right":2}` | complete; flagged; business.limit; recommend block / effective allow |
| `date.leap_day.input` | `{"left": "2024-02-29", "right": "2024-03-01"}` | complete; passed; no finding; recommend allow / effective allow |
| `date.leap_day.output` | `{"left": "2024-02-29", "right": "2024-03-01"}` | complete; passed; no finding; recommend allow / effective allow |
| `date.equal_strict.input` | `{"left": "2026-10-28", "right": "2026-10-28"}` | complete; flagged; business.limit; recommend flag / effective allow |
| `date.equal_strict.output` | `{"left": "2026-10-28", "right": "2026-10-28"}` | complete; flagged; business.limit; recommend block / effective allow |
| `date.invalid_day.input` | `{"left": "2026-02-30", "right": "2026-03-01"}` | complete; flagged; business.limit; recommend flag / effective allow |
| `date.invalid_day.output` | `{"left": "2026-02-30", "right": "2026-03-01"}` | complete; flagged; business.limit; recommend block / effective allow |
| `date.non_iso.input` | `{"left": "10/28/2026", "right": "2026-10-29"}` | complete; flagged; business.limit; recommend flag / effective allow |
| `date.non_iso.output` | `{"left": "10/28/2026", "right": "2026-10-29"}` | complete; flagged; business.limit; recommend block / effective allow |
| `date.null.input` | `{"left": null, "right": "2026-10-29"}` | complete; flagged; business.limit; recommend flag / effective allow |
| `date.null.output` | `{"left": null, "right": "2026-10-29"}` | complete; flagged; business.limit; recommend block / effective allow |
| `date.equal_inclusive.input` | `{"left": "2026-10-28", "right": "2026-10-28"}` | complete; passed; no finding; recommend allow / effective allow |
| `date.equal_inclusive.output` | `{"left": "2026-10-28", "right": "2026-10-28"}` | complete; passed; no finding; recommend allow / effective allow |
| `pointer.escaped_valid.input` | `{"orders": [{"a/b": 1}], "c~d": 2}` | complete; passed; no finding; recommend allow / effective allow |
| `pointer.escaped_valid.output` | `{"orders": [{"a/b": 1}], "c~d": 2}` | complete; passed; no finding; recommend allow / effective allow |
| `pointer.escaped_exceeds.input` | `{"orders": [{"a/b": 3}], "c~d": 2}` | complete; flagged; business.limit; recommend flag / effective allow |
| `pointer.escaped_exceeds.output` | `{"orders": [{"a/b": 3}], "c~d": 2}` | complete; flagged; business.limit; recommend block / effective allow |
| `pointer.missing_index.input` | `{"orders": [], "c~d": 2}` | complete; flagged; business.limit; recommend flag / effective allow |
| `pointer.missing_index.output` | `{"orders": [], "c~d": 2}` | complete; flagged; business.limit; recommend block / effective allow |
| `pointer.leading_zero_index.input` | `{"orders": [{"a/b": 1}], "c~d": 2}` | complete; flagged; business.limit; recommend flag / effective allow |
| `pointer.leading_zero_index.output` | `{"orders": [{"a/b": 1}], "c~d": 2}` | complete; flagged; business.limit; recommend block / effective allow |
| `json.duplicate.input` | `{"left":1,"left":2,"right":3}` | complete; flagged; business.invalid_json; recommend flag / effective allow |
| `json.duplicate.output` | `{"left":1,"left":2,"right":3}` | complete; flagged; business.invalid_json; recommend block / effective allow |
| `json.nonfinite.input` | `{"left":NaN,"right":2}` | complete; flagged; business.invalid_json; recommend flag / effective allow |
| `json.nonfinite.output` | `{"left":NaN,"right":2}` | complete; flagged; business.invalid_json; recommend block / effective allow |
| `json.overflow.input` | `{"left":1e400,"right":2}` | complete; flagged; business.invalid_json; recommend flag / effective allow |
| `json.overflow.output` | `{"left":1e400,"right":2}` | complete; flagged; business.invalid_json; recommend block / effective allow |
| `json.truncated.input` | `{"left":` | complete; flagged; business.invalid_json; recommend flag / effective allow |
| `json.truncated.output` | `{"left":` | complete; flagged; business.invalid_json; recommend block / effective allow |
| `format.text.input` | `left: 1 / right: 2` | incomplete; skipped; no finding; recommend block / effective allow |
| `format.text.output` | `left: 1 / right: 2` | incomplete; skipped; no finding; recommend block / effective allow |
| `rules.absent.input` | `{"left":1,"right":2}` | incomplete; skipped; no finding; recommend block / effective allow |
| `rules.absent.output` | `{"left":1,"right":2}` | incomplete; skipped; no finding; recommend block / effective allow |
| `sections.valid.input` | `Summary: Customer requests return instructions.` | complete; passed; no finding; recommend allow / effective allow |
| `sections.valid.output` | `Summary: Return instructions are available. / Next steps: Contact the store.` | complete; passed; no finding; recommend allow / effective allow |
| `sections.missing.input` | `Details: Required summary absent.` | complete; flagged; completeness.request_summary; recommend flag / effective allow |
| `sections.missing.output` | `Summary: Instructions available.` | complete; flagged; completeness.next_steps; recommend block / effective allow |
| `sections.empty.input` | `Summary:   ` | complete; flagged; completeness.request_summary; recommend flag / effective allow |
| `sections.empty.output` | `Summary:    / Next steps: Contact the store.` | complete; flagged; completeness.response_summary; recommend block / effective allow |
| `sections.duplicate.input` | `Summary: First summary. / SUMMARY: Second summary.` | complete; flagged; completeness.request_summary; recommend flag / effective allow |
| `sections.duplicate.output` | `Summary: Instructions available. / Next steps: Contact store. / Next steps: Open ticket.` | complete; flagged; completeness.next_steps; recommend block / effective allow |
| `sections.short.input` | `Summary: Hi` | complete; passed; no finding; recommend allow / effective allow |
| `sections.short.output` | `Summary: Instructions available. / Next steps: Hi` | complete; flagged; completeness.next_steps; recommend block / effective allow |
| `sections.unknown_boundary.input` | `Summary: / Other: Not the summary.` | complete; flagged; completeness.request_summary; recommend flag / effective allow |
| `sections.unknown_boundary.output` | `Summary: Instructions available. / Next steps: / Other: Not the next step.` | complete; flagged; completeness.next_steps; recommend block / effective allow |
| `sections.multiline.input` | `Summary: / Customer requests instructions.` | complete; passed; no finding; recommend allow / effective allow |
| `sections.multiline.output` | `Summary: / Instructions are available. / Next steps: / Contact the store.` | complete; passed; no finding; recommend allow / effective allow |
| `sections.json.input` | `{"Summary":"This is JSON, not labeled text."}` | incomplete; skipped; no finding; recommend block / effective allow |
| `sections.json.output` | `{"Summary":"This is JSON, not labeled text."}` | incomplete; skipped; no finding; recommend block / effective allow |
