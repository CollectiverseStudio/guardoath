# Graph structure — independent findings and fail-closed acceptance

**Scope:** Local syntactic checks of a submitted directed graph, for JSON messages only. These checks do not prove edge truth, authorization, provenance, or freedom from harmful instructions. The current checker is `graph` in `guardoath/data_quality.py`; the baseline policy is `policies/graph-batch.json`.

## Graph use cases and required outcomes

| Contract | Representative synthetic example | Result when complete | Acceptance evidence |
|---|---|---|---|
| Unique node identifiers | `nodes=["A","A"]` | Incomplete / `graph_duplicate_node`, fail-closed Block | `test_invalid_graph_limits_or_duplicate_nodes_fail_closed` |
| Referential integrity | `nodes=["A","B"]; edges=[["A","Z"]]` | `structure.graph_unknown_node` | `test_all_independent_graph_violations_survive_engine` |
| Duplicate directed edges | `edges=[["A","B"],["A","B"]]` | `structure.graph_duplicate_edge` | `test_all_independent_graph_violations_survive_engine` |
| Acyclic directed dependencies | `A -> B -> A` | `structure.graph_cycle` | `test_all_independent_graph_violations_survive_engine` |
| Self-loop rejection | `A -> A` | `structure.graph_cycle` | `test_self_loop_and_diamond_dag_are_not_confused` |
| Multiple independent failures | Duplicate edge and unknown endpoint, or duplicate edge and cycle | Return *both* applicable finding IDs, never first only | `test_all_independent_graph_violations_survive_engine` |
| Benign converging DAG | `A -> B, A -> C, B -> D, C -> D` | Complete / Allow | `test_self_loop_and_diamond_dag_are_not_confused` |
| Maximum graph size | 100 declared nodes, 500 distinct acyclic edges | Complete / Allow | `test_graph_at_configured_node_and_edge_limits_is_allowed` |
| Oversized graph | 101 nodes or 501 edges under a 100/500 policy | Incomplete / `graph_size_limit`, fail-closed Block | `test_invalid_graph_limits_or_duplicate_nodes_fail_closed` |
| Malformed or hostile JSON | Truncation, duplicate decoded keys, non-finite values, invalid edge item types | Incomplete / `graph_invalid_document`, fail-closed Block | `test_invalid_graph_documents_fail_closed_without_raw_value_leak` |
| Wrong message format | Text sent to a JSON-only Graph check | Skipped checker / incomplete required evaluation / fail-closed Block | `test_text_instead_of_json_is_not_a_successful_graph_validation` |
| Safe findings | Synthetic private labels on declared nodes | No raw node/edge names in response | `test_graph_findings_do_not_echo_submitted_identifiers` |

## Configuration and actions

The existing `graph-enforce-v1` profile is `mode=enforce` with a required `graph` checker, `max_nodes=100`, and `max_edges=500`. Completed Input findings are configured to **Flag**, while completed Output findings are configured to **Block**. Incomplete required checks use the policy `failure_action=block` on both stages. These independent stage actions are intentional and the regression tests must preserve them.

## Boundaries, privacy, and release criteria

The checker validates only the declared nodes and edges of the submitted document. It does not query an external graph database, retrieve trusted identifiers, authorize relationships, determine whether a causal/dependency edge is legitimate, or execute a graph. Graph-size restrictions are **per-message structural resource controls**, unrelated to the rejected 50-comparisons-per-tenant product limitation in the separate Field Comparisons category.

Test fixtures use synthetic names only. Explanations and response metadata must not echo graph IDs, unknown references, or unrelated content. Partial coverage, parser errors, unsupported formats, duplicate-node errors and resource exhaustion must never silently count as successful evaluation.

**Evidence sources:** `tests/test_graph_multi_finding_independence.py`, `tests/test_data_quality.py`, `evals/graph.json`, and `docs/data-quality-coverage.md`. The baseline multi-finding batch was verified with 13/13 GitHub Tests and 3/3 CodeQL at `8afd6f0691a07684386e31599fb0221e37301cf7`. The later malformed-JSON additions in `2ec7ad72b939a9c8e1b3318bd8e55f0f17fade6a` require **their own exact-head CI**; do not infer their passing status from the baseline. No production deployment is authorized solely by these tests.
