import type { AnswerType, Question } from "../../api/types";

const TYPES: { value: AnswerType; label: string }[] = [
  { value: "text", label: "Free text" },
  { value: "yes_no", label: "Yes / No" },
  { value: "rating", label: "Rating (1–5)" },
  { value: "number", label: "Number" },
  { value: "choice", label: "Multiple choice" },
];

export const emptyQuestion = (): Question => ({
  key: "", text: "", answer_type: "text", options: [], extraction_hint: null, is_required: true,
});

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40);

export default function QuestionEditor({
  questions, onChange, disabled,
}: { questions: Question[]; onChange: (q: Question[]) => void; disabled?: boolean }) {
  const update = (i: number, patch: Partial<Question>) =>
    onChange(questions.map((q, idx) => (idx === i ? { ...q, ...patch } : q)));
  const move = (i: number, dir: -1 | 1) => {
    const next = [...questions];
    [next[i], next[i + dir]] = [next[i + dir], next[i]];
    onChange(next);
  };

  return (
    <div>
      {questions.map((q, i) => (
        <div className="question-card" key={i}>
          <div className="row spread">
            <strong>Q{i + 1}</strong>
            {!disabled && (
              <div className="row">
                <button type="button" className="btn ghost sm" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                <button type="button" className="btn ghost sm" disabled={i === questions.length - 1} onClick={() => move(i, 1)}>↓</button>
                <button type="button" className="btn ghost sm" onClick={() => onChange(questions.filter((_, idx) => idx !== i))}>Remove</button>
              </div>
            )}
          </div>
          <div className="form" style={{ marginTop: 8 }}>
            <label>
              Question (spoken by the AI)
              <input
                value={q.text}
                disabled={disabled}
                placeholder="How satisfied are you with our product?"
                // Hindi text produces no Latin slug, so fall back to q1, q2, …
                onChange={(e) => update(i, { text: e.target.value, key: q.key || slug(e.target.value) || `q${i + 1}` })}
              />
            </label>
            <div className="form-row">
              <label>
                Key (column name in results)
                <input value={q.key} disabled={disabled} pattern="[a-z0-9_]{1,64}" onChange={(e) => update(i, { key: slug(e.target.value) })} />
              </label>
              <label>
                Answer type
                <select value={q.answer_type} disabled={disabled} onChange={(e) => update(i, { answer_type: e.target.value as AnswerType })}>
                  {TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </label>
              <label className="checkbox" style={{ alignSelf: "end" }}>
                <input type="checkbox" checked={q.is_required} disabled={disabled} onChange={(e) => update(i, { is_required: e.target.checked })} /> Required
              </label>
            </div>
            {q.answer_type === "choice" && (
              <label>
                Options (comma separated)
                <input
                  defaultValue={q.options.join(", ")}
                  disabled={disabled}
                  onBlur={(e) => update(i, { options: e.target.value.split(",").map((o) => o.trim()).filter(Boolean) })}
                />
              </label>
            )}
            <label>
              Extraction hint <span className="muted small">(optional — tells Gemini what to pull out)</span>
              <input
                value={q.extraction_hint ?? ""}
                disabled={disabled}
                placeholder="e.g. also capture how many months they have used it"
                onChange={(e) => update(i, { extraction_hint: e.target.value || null })}
              />
            </label>
          </div>
        </div>
      ))}
      {!disabled && (
        <button type="button" className="btn ghost" onClick={() => onChange([...questions, emptyQuestion()])}>+ Add question</button>
      )}
    </div>
  );
}

export function questionPayload(questions: Question[]) {
  return questions.map(({ key, text, answer_type, options, extraction_hint, is_required }) => ({
    key, text, answer_type, options, extraction_hint, is_required,
  }));
}
