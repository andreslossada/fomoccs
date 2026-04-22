## Summary

<!-- REQUIRED: 1-3 sentences describing WHAT changed and WHY -->

Resolves #

## Track

<!-- Check one -->
- [ ] Feature
- [ ] Bug fix
- [ ] CI/CD
- [ ] Refactor / cleanup

## Changes Made

<!-- REQUIRED: List every meaningful change. Be specific about files and what changed in them.
The reviewer agent will use this to scope its review. -->

| File | Change |
|------|--------|
|      |        |

## Implementation Decisions

<!-- REQUIRED: Explain any non-obvious technical decisions.
Why this approach over alternatives? Any trade-offs made? -->

## Testing

### Automated Tests

<!-- List tests added or modified -->
- [ ] Unit tests: `path/to/test_file.py`
- [ ] Integration tests: `path/to/test_file.py`
- [ ] E2E tests: `path/to/test_file.spec.ts`

### Manual Verification Steps

<!-- REQUIRED: Step-by-step instructions a reviewer agent can follow to verify this works.
Be extremely specific — include exact commands, URLs, expected outputs.
The reviewer will execute these literally. -->

**Setup:**
```bash
# Any setup commands needed before testing
```

**Step 1:**
```bash
# Command to run
```
Expected output:
```
# What the reviewer should see
```

**Step 2:**
```bash
# Next verification command
```
Expected output:
```
# Expected result
```

### Edge Cases Verified

<!-- List edge cases you tested and their outcomes -->
- [ ] Empty input: ...
- [ ] Invalid input: ...
- [ ] Large payload: ...

## CI/CD Checklist

<!-- Only for track:cicd PRs. Delete this section otherwise. -->
- [ ] Workflow tested locally (act or manual run)
- [ ] No secrets hardcoded
- [ ] Failure notifications configured
- [ ] Workflow trigger conditions are correct

## Reviewer Agent Instructions

<!-- DO NOT EDIT: Standard review protocol -->
When reviewing this PR:

1. **Read the linked issue** for full context on requirements
2. **Check the Changes Made table** against actual diff — flag any unlisted changes
3. **Execute Manual Verification Steps** exactly as written — report any deviation from expected output
4. **Verify Acceptance Criteria** from the linked issue are met
5. **Check for**:
   - Security: SQL injection, XSS, secrets in code, unsafe deserialization
   - Performance: N+1 queries, unbounded loops, missing pagination
   - Error handling: unhandled exceptions, missing validation at boundaries
   - Breaking changes: API contract changes, removed fields, type changes
6. **Run the test suite**:
   ```bash
   cd backend && uv run pytest  # Backend tests
   npm test                      # Frontend/E2E tests
   ```
7. **Approve only if** all verification steps pass and no blocking issues found
