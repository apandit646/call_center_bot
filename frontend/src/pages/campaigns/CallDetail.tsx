import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../../api/client";
import type { CallDetail as TCall, Campaign } from "../../api/types";
import { Badge, ErrorBox, Loading, PageHeader, fmtDate, fmtValue } from "../../components/ui";

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
  const questionById = new Map(campaign?.questions.map((q) => [q.id, q]) ?? []);

  return (
    <>
      <PageHeader
        title={<>Call to {call.customer.name ?? call.customer.phone} <Badge value={call.status} /></>}
        subtitle={<>
          {call.customer.phone} · attempt {call.attempt_number} · {call.provider}
          {campaign && <> · <Link to={`/campaigns/${campaign.id}`}>{campaign.name}</Link></>}
        </>}
      />
      {call.error && <div className="error">{call.error}</div>}
      <div className="grid grid-2">
        <div>
          <div className="card">
            <h2>Structured answers</h2>
            {!call.answers.length ? <p className="muted">No answers recorded.</p> : (
              <div className="table-wrap"><table>
                <thead><tr><th>Question</th><th>Answer</th><th>Confidence</th></tr></thead>
                <tbody>{call.answers.map((a) => (
                  <tr key={a.question_id}>
                    <td className="small">{questionById.get(a.question_id)?.text ?? a.question_id}</td>
                    <td>
                      <strong>{fmtValue(a.normalized_answer.value)}</strong> <Badge value={a.status} />
                      {a.raw_answer && <div className="small muted">“{a.raw_answer}”</div>}
                      {Object.entries(a.normalized_answer).filter(([k]) => k !== "value").map(([k, v]) => (
                        <div key={k} className="small">{k}: {fmtValue(v)}</div>
                      ))}
                    </td>
                    <td>{a.confidence != null ? `${Math.round(a.confidence * 100)}%` : "—"}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            )}
          </div>
          <div className="card">
            <h2>Call info</h2>
            <div className="small">Started: {fmtDate(call.started_at)}</div>
            <div className="small">Answered: {fmtDate(call.answered_at)}</div>
            <div className="small">Ended: {fmtDate(call.ended_at)}</div>
            <div className="small">Duration: {call.duration_seconds != null ? `${call.duration_seconds}s` : "—"}</div>
            {audioUrl ? <audio controls src={audioUrl} style={{ width: "100%", marginTop: 12 }} /> : (
              <p className="muted small">{call.has_recording ? "Loading recording…" : "No recording available."}</p>
            )}
          </div>
        </div>
        <div className="card">
          <h2>Transcript</h2>
          <div className="chat">
            {call.transcript.map((t) => (
              <div key={t.turn_index} className={`bubble ${t.speaker === "ai" ? "outbound" : "inbound"}`}>
                {t.text}
                <div className="meta">{t.speaker === "ai" ? "AI voice" : "Customer"} · {fmtDate(t.created_at)}</div>
              </div>
            ))}
            {!call.transcript.length && <p className="muted">No transcript yet.</p>}
          </div>
        </div>
      </div>
    </>
  );
}
