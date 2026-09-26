import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { CallDetail as TCall, Campaign } from "../../api/types";
import { IconBot, IconChat, IconClock, IconGlobe, IconPhone } from "../../components/Icons";
import { Badge, Empty, ErrorBox, Loading, PageHeader, fmtDate, fmtValue } from "../../components/ui";
import { LANGUAGE_NAMES } from "./QuestionEditor";

const fmtTime = (v: string) => new Date(v).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });

function Confidence({ value }: { value: number | null }) {
  if (value == null) return null;
  const pct = Math.round(value * 100);
  const tone = pct >= 75 ? "green" : pct >= 45 ? "amber" : "red";
  return (
    <div className="row" style={{ gap: 8 }} title="How sure the AI is about the extracted answer">
      <div className={`bar ${tone}`} style={{ width: 90 }}><div style={{ width: `${pct}%` }} /></div>
      <span className="small muted">{pct}% confidence</span>
    </div>
  );
}

export default function CallDetail() {
  const { id } = useParams();
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  const { data: call, isLoading, error } = useQuery({
    queryKey: ["call", id],
    queryFn: async () => (await api.get<TCall>(`/calls/${id}`)).data,
    refetchInterval: (q) => (["initiated", "ringing", "in_progress"].includes(q.state.data?.status ?? "") ? 3_000 : false),
  });
  const { data: campaign } = useQuery({
    queryKey: ["campaign", call?.campaign_id],
    queryFn: async () => (await api.get<Campaign>(`/campaigns/${call!.campaign_id}`)).data,
    enabled: !!call,
  });

  // The recording endpoint needs the JWT, so fetch it as a blob instead of pointing <audio> at it.
  useEffect(() => {
    if (!call?.has_recording) return;
    let url: string | null = null;
    api.get(`/calls/${call.id}/recording`, { responseType: "blob" }).then((res) => {
      url = URL.createObjectURL(res.data as Blob);
      setAudioUrl(url);
    });
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [call?.id, call?.has_recording]);

  if (isLoading) return <Loading />;
  if (error || !call) return <ErrorBox error={error} />;

  const questions = campaign?.questions ?? [];
  const answerFor = new Map(call.answers.map((a) => [a.question_id, a]));
  const ordered = questions.length
    ? questions.filter((q) => q.id && answerFor.has(q.id)).map((q) => ({ q, a: answerFor.get(q.id!)! }))
    : call.answers.map((a) => ({ q: undefined, a }));
  const language = call.language ? LANGUAGE_NAMES[call.language] ?? call.language : null;

  return (
    <>
      <PageHeader
        title={<>{call.customer.name ?? call.customer.phone} <Badge value={call.status} /></>}
        subtitle={<>
          {call.customer.phone}
          {campaign && <> · <Link to={`/campaigns/${campaign.id}`}>{campaign.name}</Link></>}
        </>}
        actions={
          <div className="row">
            <span className="pill"><IconPhone size={14} /> Attempt {call.attempt_number}</span>
            <span className="pill"><IconClock size={14} /> {call.duration_seconds != null ? `${call.duration_seconds}s` : "—"}</span>
            {language && <span className="pill"><IconGlobe size={14} /> Ended in {language}</span>}
          </div>
        }
      />
      <ErrorBox error={call.error ? new Error(call.error) : null} />

      <div className="grid grid-2">
        <div>
          <div className="card">
            <div className="section-title">
              <h2><span className="section-icon"><IconBot size={16} /></span> Answers</h2>
              {call.all_questions_answered && <Badge value="all answered" tone="green" />}
            </div>
            {!ordered.length ? <Empty>No answers recorded yet.</Empty> : ordered.map(({ q, a }) => {
              const extras = Object.entries(a.normalized_answer).filter(([k]) => k !== "value");
              return (
                <div className="answer-card" key={a.question_id}>
                  <div className="answer-q">{q?.text ?? "Question"}</div>
                  <div className="answer-value">
                    {fmtValue(a.normalized_answer.value)} <Badge value={a.status} />
                  </div>
                  {a.raw_answer && <div className="answer-quote">“{a.raw_answer}”</div>}
                  {extras.length > 0 && (
                    <div className="row">{extras.map(([k, v]) => <span key={k} className="pill">{k.replace(/_/g, " ")}: {fmtValue(v)}</span>)}</div>
                  )}
                  <Confidence value={a.confidence} />
                </div>
              );
            })}
          </div>

          <div className="card">
            <h2>Call details</h2>
            <dl className="meta-list">
              <dt>Provider</dt><dd>{call.provider}{call.provider_call_id && <span className="muted small"> · {call.provider_call_id}</span>}</dd>
              <dt>Started</dt><dd>{fmtDate(call.started_at)}</dd>
              <dt>Answered</dt><dd>{fmtDate(call.answered_at)}</dd>
              <dt>Ended</dt><dd>{fmtDate(call.ended_at)}</dd>
            </dl>
            <div style={{ marginTop: 16 }}>
              {audioUrl ? <audio controls src={audioUrl} style={{ width: "100%" }} /> : (
                <p className="muted small" style={{ margin: 0 }}>{call.has_recording ? "Loading recording…" : "No recording available."}</p>
              )}
            </div>
          </div>
        </div>

        <div className="card">
          <div className="section-title">
            <h2><span className="section-icon"><IconChat size={16} /></span> Transcript</h2>
            <span className="small muted">{call.transcript.length} turns</span>
          </div>
          <div className="chat">
            {call.transcript.map((t) => (
              <div key={t.turn_index} className={`turn ${t.speaker === "ai" ? "ai" : "customer"}`}>
                {t.speaker === "ai"
                  ? <img className="turn-avatar" src="/logo.png" alt="AI agent" />
                  : <div className="turn-avatar">{(call.customer.name ?? "C")[0].toUpperCase()}</div>}
                <div className="bubble">
                  {t.text}
                  <div className="meta">{t.speaker === "ai" ? "AI voice" : "Customer"} · {fmtTime(t.created_at)}</div>
                </div>
              </div>
            ))}
            {!call.transcript.length && <Empty>No transcript yet.</Empty>}
          </div>
        </div>
      </div>
    </>
  );
}
