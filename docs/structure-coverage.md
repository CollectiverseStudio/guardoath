# Structure group regression examples

100 expectations: 50 scenarios on both stages. Four intentionally require an incomplete evaluation; two require optional skips. A matched skipped/incomplete expectation is not a data assessment. Fixtures are synthetic. Full inputs are in evals/structure.json.

| Case | Example / use case | Expected result |
| --- | --- | --- |
| `valid.base.input` | valid / base | complete; passed; no finding; recommend allow / effective allow |
| `valid.base.output` | valid / base | complete; passed; no finding; recommend allow / effective allow |
| `valid.three_items.input` | valid / three items | complete; passed; no finding; recommend allow / effective allow |
| `valid.three_items.output` | valid / three items | complete; passed; no finding; recommend allow / effective allow |
| `valid.minimum.input` | valid / minimum; content length 5 | complete; passed; no finding; recommend allow / effective allow |
| `valid.minimum.output` | valid / minimum; content length 5 | complete; passed; no finding; recommend allow / effective allow |
| `valid.maximum.input` | valid / maximum; content length 500 | complete; passed; no finding; recommend allow / effective allow |
| `valid.maximum.output` | valid / maximum; content length 500 | complete; passed; no finding; recommend allow / effective allow |
| `valid.null_note.input` | valid / null note | complete; passed; no finding; recommend allow / effective allow |
| `valid.null_note.output` | valid / null note | complete; passed; no finding; recommend allow / effective allow |
| `valid.string_note.input` | valid / string note | complete; passed; no finding; recommend allow / effective allow |
| `valid.string_note.output` | valid / string note | complete; passed; no finding; recommend allow / effective allow |
| `valid.unicode.input` | valid / unicode | complete; passed; no finding; recommend allow / effective allow |
| `valid.unicode.output` | valid / unicode | complete; passed; no finding; recommend allow / effective allow |
| `valid.escaped_string.input` | valid / escaped string | complete; passed; no finding; recommend allow / effective allow |
| `valid.escaped_string.output` | valid / escaped string | complete; passed; no finding; recommend allow / effective allow |
| `root.null.input` | root / null: None | complete; flagged; schema.invalid; recommend block / effective allow |
| `root.null.output` | root / null: None | complete; flagged; schema.invalid; recommend block / effective allow |
| `root.array.input` | root / array: [] | complete; flagged; schema.invalid; recommend block / effective allow |
| `root.array.output` | root / array: [] | complete; flagged; schema.invalid; recommend block / effective allow |
| `root.string.input` | root / string: A plain string | complete; flagged; schema.invalid; recommend block / effective allow |
| `root.string.output` | root / string: A plain string | complete; flagged; schema.invalid; recommend block / effective allow |
| `missing.content.input` | missing / content | complete; flagged; schema.invalid; recommend block / effective allow |
| `missing.content.output` | missing / content | complete; flagged; schema.invalid; recommend block / effective allow |
| `extra.root.input` | extra / root | complete; flagged; schema.invalid; recommend block / effective allow |
| `extra.root.output` | extra / root | complete; flagged; schema.invalid; recommend block / effective allow |
| `type.content.input` | type / content | complete; flagged; schema.invalid; recommend block / effective block |
| `type.content.output` | type / content | complete; flagged; schema.invalid; recommend block / effective block |
| `type.boolean_integer.input` | type / boolean integer; sequence 10; item {"sku": "ABC-123", "quantity": true} | complete; flagged; schema.invalid; recommend block / effective allow |
| `type.boolean_integer.output` | type / boolean integer; sequence 10; item {"sku": "ABC-123", "quantity": true} | complete; flagged; schema.invalid; recommend block / effective allow |
| `format.uuid.input` | format / uuid: not-a-uuid | complete; flagged; schema.invalid; recommend block / effective allow |
| `format.uuid.output` | format / uuid: not-a-uuid | complete; flagged; schema.invalid; recommend block / effective allow |
| `format.email.input` | format / email: not-an-email | complete; flagged; schema.invalid; recommend block / effective allow |
| `format.email.output` | format / email: not-an-email | complete; flagged; schema.invalid; recommend block / effective allow |
| `format.date_time.input` | format / date time: 2026-10-28 12:30:00 | complete; flagged; schema.invalid; recommend block / effective allow |
| `format.date_time.output` | format / date time: 2026-10-28 12:30:00 | complete; flagged; schema.invalid; recommend block / effective allow |
| `format.date.input` | format / date: 2026-02-30 | complete; flagged; schema.invalid; recommend block / effective allow |
| `format.date.output` | format / date: 2026-02-30 | complete; flagged; schema.invalid; recommend block / effective allow |
| `format.time.input` | format / time: 25:30:00Z | complete; flagged; schema.invalid; recommend block / effective allow |
| `format.time.output` | format / time: 25:30:00Z | complete; flagged; schema.invalid; recommend block / effective allow |
| `format.ipv4.input` | format / ipv4: 999.1.2.3 | complete; flagged; schema.invalid; recommend block / effective allow |
| `format.ipv4.output` | format / ipv4: 999.1.2.3 | complete; flagged; schema.invalid; recommend block / effective allow |
| `format.ipv6.input` | format / ipv6: not-an-ipv6 | complete; flagged; schema.invalid; recommend block / effective allow |
| `format.ipv6.output` | format / ipv6: not-an-ipv6 | complete; flagged; schema.invalid; recommend block / effective allow |
| `enum.choice.input` | enum / choice | complete; flagged; schema.invalid; recommend block / effective allow |
| `enum.choice.output` | enum / choice | complete; flagged; schema.invalid; recommend block / effective allow |
| `array.empty.input` | array / empty; item count 0 | complete; flagged; schema.invalid; recommend block / effective allow |
| `array.empty.output` | array / empty; item count 0 | complete; flagged; schema.invalid; recommend block / effective allow |
| `array.too_many.input` | array / too many; item count 4 | complete; flagged; schema.invalid; recommend block / effective allow |
| `array.too_many.output` | array / too many; item count 4 | complete; flagged; schema.invalid; recommend block / effective allow |
| `array.duplicate.input` | array / duplicate; item count 2 | complete; flagged; schema.invalid; recommend block / effective allow |
| `array.duplicate.output` | array / duplicate; item count 2 | complete; flagged; schema.invalid; recommend block / effective allow |
| `missing.nested.input` | missing / nested | complete; flagged; schema.invalid; recommend block / effective allow |
| `missing.nested.output` | missing / nested | complete; flagged; schema.invalid; recommend block / effective allow |
| `extra.nested.input` | extra / nested | complete; flagged; schema.invalid; recommend block / effective allow |
| `extra.nested.output` | extra / nested | complete; flagged; schema.invalid; recommend block / effective allow |
| `pattern.sku.input` | pattern / sku; sequence 10; item {"sku": "abc-123", "quantity": 2} | complete; flagged; schema.invalid; recommend block / effective allow |
| `pattern.sku.output` | pattern / sku; sequence 10; item {"sku": "abc-123", "quantity": 2} | complete; flagged; schema.invalid; recommend block / effective allow |
| `range.minimum.input` | range / minimum; sequence 10; item {"sku": "ABC-123", "quantity": 0} | complete; flagged; schema.invalid; recommend block / effective allow |
| `range.minimum.output` | range / minimum; sequence 10; item {"sku": "ABC-123", "quantity": 0} | complete; flagged; schema.invalid; recommend block / effective allow |
| `range.maximum.input` | range / maximum; sequence 10; item {"sku": "ABC-123", "quantity": 11} | complete; flagged; schema.invalid; recommend block / effective allow |
| `range.maximum.output` | range / maximum; sequence 10; item {"sku": "ABC-123", "quantity": 11} | complete; flagged; schema.invalid; recommend block / effective allow |
| `number.multiple.input` | number / multiple; sequence 7; item {"sku": "ABC-123", "quantity": 2} | complete; flagged; schema.invalid; recommend block / effective allow |
| `number.multiple.output` | number / multiple; sequence 7; item {"sku": "ABC-123", "quantity": 2} | complete; flagged; schema.invalid; recommend block / effective allow |
| `length.short.input` | length / short; content length 2 | complete; flagged; schema.invalid; recommend block / effective allow |
| `length.short.output` | length / short; content length 2 | complete; flagged; schema.invalid; recommend block / effective allow |
| `length.long.input` | length / long; content length 501 | complete; flagged; schema.invalid; recommend block / effective allow |
| `length.long.output` | length / long; content length 501 | complete; flagged; schema.invalid; recommend block / effective allow |
| `json.truncated.input` | json / truncated: {"request_id": | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.truncated.output` | json / truncated: {"request_id": | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.comment.input` | json / comment: {/*comment*/"x":1} | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.comment.output` | json / comment: {/*comment*/"x":1} | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.trailing_comma.input` | json / trailing comma: {"x":1,} | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.trailing_comma.output` | json / trailing comma: {"x":1,} | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.single_quote.input` | json / single quote: {'x':1} | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.single_quote.output` | json / single quote: {'x':1} | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.nan.input` | json / nan | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.nan.output` | json / nan | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.infinity.input` | json / infinity | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.infinity.output` | json / infinity | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.overflow.input` | json / overflow | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.overflow.output` | json / overflow | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.duplicate.input` | json / duplicate | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.duplicate.output` | json / duplicate | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.escaped_duplicate.input` | json / escaped duplicate | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.escaped_duplicate.output` | json / escaped duplicate | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.surrogate.input` | json / surrogate | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.surrogate.output` | json / surrogate | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.number_limit.input` | json / number limit | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.number_limit.output` | json / number limit | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.depth_limit.input` | json / depth limit | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.depth_limit.output` | json / depth limit | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.container_limit.input` | json / container limit | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `json.container_limit.output` | json / container limit | complete; flagged; schema.invalid_json; recommend block / effective allow |
| `text.required.input` | text / required: This is text, not a selected JSON document. | incomplete; skipped; no finding; recommend block / effective allow |
| `text.required.output` | text / required: This is text, not a selected JSON document. | incomplete; skipped; no finding; recommend block / effective allow |
| `text.optional.input` | text / optional: No schema assessment was performed for this text. | complete; skipped; no finding; recommend allow / effective allow |
| `text.optional.output` | text / optional: No schema assessment was performed for this text. | complete; skipped; no finding; recommend allow / effective allow |
| `schema.unconfigured.input` | schema / unconfigured | incomplete; skipped; no finding; recommend block / effective allow |
| `schema.unconfigured.output` | schema / unconfigured | incomplete; skipped; no finding; recommend block / effective allow |
