# diagnosis

The investigation of one incident with Claude: from the evidence, list hypotheses, run the checks that separate them, conclude with a report.

**Will contain**

- The evidence given to the model, the investigation loop, the LLM client, the prompt (generic part).
- The report: root cause, evidence for and against, alternatives ruled out and how, confidence, repair steps.

**Must not contain**

- Machine-specific prompt text (it comes from the machine profile), direct calls to a machine.

**Depends on:** contracts, storage, knowledge, safety
