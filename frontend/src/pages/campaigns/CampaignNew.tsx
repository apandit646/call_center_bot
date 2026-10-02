import { useEffect, useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import type { Campaign, OfferingType, Question } from "../../api/types";
import { useCompany } from "../../hooks";
import { ErrorBox, PageHeader } from "../../components/ui";
import QuestionEditor, { emptyQuestion, questionPayload } from "./QuestionEditor";

// Languages Sarvam Bulbul can speak. hi-IN scripts can freely mix English words (Hinglish).
export const VOICE_LANGUAGES = [
  { code: "en-IN", label: "English (India)" },
  { code: "hi-IN", label: "Hindi + English (Hinglish)" },
  { code: "mr-IN", label: "Marathi" },
  { code: "gu-IN", label: "Gujarati" },
  { code: "bn-IN", label: "Bengali" },
  { code: "pa-IN", label: "Punjabi" },
  { code: "ta-IN", label: "Tamil" },
  { code: "te-IN", label: "Telugu" },
  { code: "kn-IN", label: "Kannada" },
  { code: "ml-IN", label: "Malayalam" },
  { code: "od-IN", label: "Odia" },
];

interface Template {
  greeting: string;
  closing: string;
  questions: Question[];
}

const q = (key: string, text: string, answer_type: Question["answer_type"], is_required = true): Question => ({
  ...emptyQuestion(), key, text, answer_type, is_required,
});

// Hinglish is written the way it is spoken: Hindi in Devanagari, everyday English words left in English.
const TEMPLATES: Record<"hi-IN" | "en-IN", Template> = {
  "hi-IN": {
    greeting: "नमस्ते {name} जी, हम {company} की तरफ़ से बात कर रहे हैं। आपसे कुछ quick questions पूछने हैं।",
    closing: "आपके time के लिए बहुत-बहुत धन्यवाद। Have a great day!",
    questions: [
      q("using_product", "क्या आप अभी हमारा product use कर रहे हैं?", "yes_no"),
      q("satisfaction", "1 से 5 के scale पर, आप हमारी service से कितने satisfied हैं?", "rating"),
      q("problem", "क्या आपको कोई problem आ रही है? बताइए।", "text", false),
      q("recommend", "क्या आप हमारा product अपने friends को recommend करेंगे?", "yes_no"),
      q("callback", "क्या आप चाहेंगे कि हमारी team आपको callback करे?", "yes_no"),
    ],
  },
  "en-IN": {
    greeting: "Hello {name}, this is an automated call from {company}. We have a few quick questions for you.",
    closing: "Thank you for your time. Have a great day!",
    questions: [
      q("using_product", "Are you currently using our product?", "yes_no"),
      q("satisfaction", "On a scale of 1 to 5, how satisfied are you?", "rating"),
      q("problem", "What problem, if any, are you facing?", "text", false),
      q("recommend", "Would you recommend our product to others?", "yes_no"),
      q("callback", "Would you like a callback from our team?", "yes_no"),
    ],
  },
};

export const OFFERING_LABEL: Record<OfferingType, string> = { product: "A product", service: "A service", general: "General (no specific product)" };

export default function CampaignNew() {
  const navigate = useNavigate();
  const { data: company } = useCompany();
  const limitReached = !!company?.usage && company.usage.max_campaigns !== null && company.usage.campaigns >= company.usage.max_campaigns;
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [offeringType, setOfferingType] = useState<OfferingType>("product");
  const [offeringName, setOfferingName] = useState("");
  const [offeringDetails, setOfferingDetails] = useState("");
  // Calls start in English and switch to Hindi if the customer answers in Hindi.
  const [language, setLanguage] = useState("en-IN");
  const [switchToHindi, setSwitchToHindi] = useState(true);
  const [conversational, setConversational] = useState(true);
  const [agentPrompt, setAgentPrompt] = useState("");
  // Pre-fill the default personality so it can be tweaked per campaign.
  useEffect(() => {
    api.get<{ agent_prompt: string }>("/campaigns/agent-prompt/default").then((r) => setAgentPrompt((p) => p || r.data.agent_prompt)).catch(() => {});
  }, []);
  const [greeting, setGreeting] = useState(TEMPLATES["en-IN"].greeting);
  const [closing, setClosing] = useState(TEMPLATES["en-IN"].closing);
  const [questions, setQuestions] = useState<Question[]>(TEMPLATES["en-IN"].questions);
  const altLanguages = switchToHindi && language !== "hi-IN" ? ["hi-IN"] : [];
  const [edited, setEdited] = useState(false);

  const changeLanguage = (code: string) => {
    setLanguage(code);
    // Swap in the matching sample script only if the admin hasn't started editing it.
    const template = TEMPLATES[code as keyof typeof TEMPLATES];
    if (template && !edited) {
      setGreeting(template.greeting);
      setClosing(template.closing);
      setQuestions(template.questions);
    }
  };

  const create = useMutation({
    mutationFn: async () =>
      (
        await api.post<Campaign>("/campaigns", {
          name, description: description || null, greeting_script: greeting, closing_script: closing, language,
          offering_type: offeringType, offering_name: offeringType === "general" ? null : offeringName || null,
          offering_details: offeringDetails || null,
          alt_languages: altLanguages, conversational, agent_prompt: conversational ? agentPrompt : null,
          questions: questionPayload(questions),
        })
      ).data,
    onSuccess: (c) => navigate(`/campaigns/${c.id}`),
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };

  return (
    <>
      <PageHeader title="New campaign" subtitle="Step 1 of 3 — define the call script. Next: import contacts, then schedule." />
      <form className="form" onSubmit={submit}>
        <ErrorBox error={create.error} />
        {limitReached && (
          <div className="error" role="alert">
            Your plan allows {company?.usage?.max_campaigns} campaign(s) and you have used all of them. Delete a draft campaign or ask the platform administrator to raise the limit.
          </div>
        )}
        <div className="card form">
          <h2>Campaign</h2>
          <label>Name<input value={name} onChange={(e) => setName(e.target.value)} required placeholder="Customer Feedback September" /></label>
          <div className="form-row">
            <label>
              This campaign is about
              <select value={offeringType} onChange={(e) => setOfferingType(e.target.value as OfferingType)}>
                {(Object.keys(OFFERING_LABEL) as OfferingType[]).map((t) => <option key={t} value={t}>{OFFERING_LABEL[t]}</option>)}
              </select>
            </label>
            {offeringType !== "general" && (
              <label>
                {offeringType === "product" ? "Product name" : "Service name"}
                <input value={offeringName} onChange={(e) => setOfferingName(e.target.value)}
                  placeholder={offeringType === "product" ? "e.g. Acme Fibre 100 Mbps plan" : "e.g. Home installation service"} />
              </label>
            )}
          </div>
          <label>
            {offeringType === "general" ? "Details the AI may share" : `About the ${offeringType}`}{" "}
            <span className="muted small">— features, price, who it is for. The AI answers the customer's questions from this and never makes things up.</span>
            <textarea value={offeringDetails} onChange={(e) => setOfferingDetails(e.target.value)}
              placeholder="e.g. Unlimited data at 100 Mbps for ₹699/month, free router, installation within 48 hours." />
          </label>
          <label>
            Why you are calling <span className="muted small">— the purpose of this call, in one or two lines</span>
            <textarea value={description} onChange={(e) => setDescription(e.target.value)}
              placeholder="e.g. Calling existing customers for feedback on the new plan. Support: 1800-123-456, 9am-9pm." />
          </label>
          <span className="muted small">
            The AI also knows your company profile{company ? ` (${company.name})` : ""} from Company Settings, so it can say who is calling.
          </span>
          <label className="checkbox">
            <input type="checkbox" checked={conversational} onChange={(e) => setConversational(e.target.checked)} />
            Natural conversation — the AI talks freely like a person, reacts to answers and asks follow-ups
          </label>
          <span className="muted small">
            {conversational
              ? "Your questions are the goals of the call: the AI covers all of them in its own words and records every answer. Replies take ~1–2 s longer than a fixed script."
              : "The AI reads the questions exactly as written, with short acknowledgements. Fastest and most predictable."}
          </span>
          {conversational && (
            <label>
              AI personality &amp; conversation rules
              <span className="muted small">How the agent should behave and sound. The questions below are its goal for the call.</span>
              <textarea style={{ minHeight: 220, fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12.5 }}
                value={agentPrompt} onChange={(e) => setAgentPrompt(e.target.value)} />
            </label>
          )}
          <label>
            Call starts in
            <select value={language} onChange={(e) => changeLanguage(e.target.value)}>
              {VOICE_LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
            </select>
          </label>
          {language !== "hi-IN" && (
            <label className="checkbox">
              <input type="checkbox" checked={switchToHindi} onChange={(e) => setSwitchToHindi(e.target.checked)} />
              Switch to Hindi if the customer answers in Hindi
            </label>
          )}
          <span className="muted small">
            {altLanguages.length
              ? "Questions are asked in the starting language. As soon as the customer replies in Hindi, the rest of the call continues in Hindi (and back again if they switch). Hindi versions are auto-translated when you schedule; you can review them in the Questions tab."
              : "The whole call is in this language. Answers in Hindi, English or a mix are all understood."}
          </span>
          <label>
            Greeting <span className="muted small">— use {"{name}"}, {"{company}"} (your company name) or any Excel column, e.g. {"{product}"}</span>
            <textarea value={greeting} onChange={(e) => { setGreeting(e.target.value); setEdited(true); }} />
          </label>
          <label>Closing<textarea value={closing} onChange={(e) => { setClosing(e.target.value); setEdited(true); }} /></label>
        </div>
        <div className="card">
          <h2>Questions</h2>
          <QuestionEditor questions={questions} altLanguages={altLanguages} onChange={(qs) => { setQuestions(qs); setEdited(true); }} />
        </div>
        <div>
          <button className="btn" disabled={create.isPending || limitReached || !name.trim() || questions.some((x) => !x.text || !x.key)}>
            {create.isPending ? "Creating…" : "Create campaign"}
          </button>
        </div>
      </form>
    </>
  );
}
