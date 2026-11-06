# Calendar-normalized metrics

Use this read-only calculation when the business metric is **quantity per entity per calendar day**.

For periods \([a,b)\) and a fixed population of \(N\) distinct entities:

\[
\text{daily average per entity}=\frac{\sum \text{quantity in period}}{N\cdot(b-a)\text{ calendar days}}
\]

The population is derived from **all rows of the selected source**, not only entities with events in the requested month. Days without events are included in the denominator. For different historical populations, the definition of the population must be agreed on before comparison.

The period endpoints must be the first day of a month, and the first period must not overlap the second. Numeric and date values are safely converted from raw \`TEXT\` using \`db/safe_cast.py\`. Invalid and empty quantities become NULL; SQL aggregates ignore them. Identifiers remain text. The raw data is not modified.

## Verification on the original case

From the repository root, with the existing local database and the configured read-only \`bi_reader\` credentials:

\`\`\`bash
python db/normalized_metrics.py \
  --table data_09_Лекарственные_препараты \
  --group ID_Лекарства \
  --entity ID_Аптеки \
  --date Дата \
  --quantity Сколько_Купили \
  --first-start 2025-02-01 --first-end 2025-03-01 \
  --second-start 2025-03-01 --second-end 2025-04-01 \
  --top 3
\`\`\`

Compare against question 1 in [TestCases.md](../TestCases.md): drug 1019, February 1.5464, March 2.0153, change +30.32%, assuming 40 pharmacies in the source. This is an **expected reference**, not a claim of a fresh database run.

The tool returns the total quantity for each period and the entity count, making the denominator inspectable. Its comparison covers groups with at least one observation in either selected period. A group with zero baseline volume has a NULL growth percentage; it is not ranked as an infinite increase.

## Automated tests

\`\`\`bash
python -m unittest discover -s db -p 'test_*.py'
\`\`\`

Tests cover leap years, full months, quarters, period overlap, zero denominators, fixed population, parameterized date boundaries, quoting of identifiers and safe conversions.

## Scope

This is a calculation and QA tool in \`db/\`, **not yet a natural-language agent feature**. The LLM/backend team can reuse the definition and checks when implementing #115 in the agent; full end-to-end acceptance remains pending.
