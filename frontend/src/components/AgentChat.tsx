import React, { useState, useRef, useEffect } from 'react';
import type { Components } from 'react-markdown';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { DashboardFilters } from '../types';
import { agentChatStream } from '../api/client';
import DeploymentDisclaimer from './v2/DeploymentDisclaimer';

interface AgentChatProps {
  filters: DashboardFilters;
}

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  thinking: string | null;
  thinkingUnavailable: boolean;
  guardrailIntervened: boolean;
}

const SUGGESTED_PROMPTS = [
  { icon: '📊', text: 'What is my total Bedrock usage and cost this week?' },
  { icon: '👤', text: 'Who are the top 5 users by token consumption?' },
  { icon: '🤖', text: 'Which models are being used most and what do they cost?' },
  { icon: '🧩', text: 'Which source applications drive the most usage?' },
  { icon: '💰', text: 'How can I optimize costs across my Bedrock usage?' },
  { icon: '🔢', text: 'Break down my input vs output vs cache token usage' },
];

// Markdown element overrides — keep spacing tight so rendered lists/tables/code
// sit naturally inside the assistant bubble and inherit its font size/color.
const MARKDOWN_COMPONENTS: Components = {
  p: (props) => <p style={{ margin: '0 0 0.5rem' }} {...props} />,
  ul: (props) => <ul style={{ margin: '0 0 0.5rem', paddingLeft: '1.25rem' }} {...props} />,
  ol: (props) => <ol style={{ margin: '0 0 0.5rem', paddingLeft: '1.25rem' }} {...props} />,
  li: (props) => <li style={{ marginBottom: '0.125rem' }} {...props} />,
  h1: (props) => <h1 style={{ fontSize: '1.05rem', margin: '0.25rem 0 0.5rem' }} {...props} />,
  h2: (props) => <h2 style={{ fontSize: '1rem', margin: '0.25rem 0 0.5rem' }} {...props} />,
  h3: (props) => <h3 style={{ fontSize: '0.9375rem', margin: '0.25rem 0 0.375rem' }} {...props} />,
  a: (props) => <a style={{ color: '#0073bb' }} target="_blank" rel="noreferrer" {...props} />,
  pre: (props) => (
    <pre
      style={{
        margin: '0 0 0.5rem',
        padding: '0.5rem 0.625rem',
        background: '#f4f4f4',
        borderRadius: '6px',
        overflowX: 'auto',
        fontSize: '0.8125rem',
      }}
      {...props}
    />
  ),
  code: ({ className, children, ...props }) => {
    const isBlock = /language-/.test(className ?? '');
    return (
      <code
        className={className}
        style={
          isBlock
            ? { fontFamily: 'monospace' }
            : {
                fontFamily: 'monospace',
                fontSize: '0.8125rem',
                background: '#ececec',
                padding: '0.05rem 0.3rem',
                borderRadius: '4px',
              }
        }
        {...props}
      >
        {children}
      </code>
    );
  },
  table: (props) => (
    <div style={{ overflowX: 'auto', margin: '0 0 0.5rem' }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '0.8125rem' }} {...props} />
    </div>
  ),
  th: (props) => (
    <th style={{ border: '1px solid #ddd', padding: '0.25rem 0.5rem', textAlign: 'left', background: '#f0f0f0' }} {...props} />
  ),
  td: (props) => <td style={{ border: '1px solid #ddd', padding: '0.25rem 0.5rem' }} {...props} />,
};

const AssistantMarkdown: React.FC<{ content: string }> = ({ content }) => (
  <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
    {content}
  </ReactMarkdown>
);

const AgentChat: React.FC<AgentChatProps> = ({ filters }) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [inputText, setInputText] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (messagesEndRef.current && typeof messagesEndRef.current.scrollIntoView === 'function') {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = inputText.trim();
    if (!trimmed || loading) return;

    const userMsgId = crypto.randomUUID();
    const assistantMsgId = crypto.randomUUID();

    // Append the user message AND an empty assistant message we stream into.
    setMessages((prev) => [
      ...prev,
      { id: userMsgId, role: 'user', content: trimmed, timestamp: new Date().toISOString(), thinking: null, thinkingUnavailable: false, guardrailIntervened: false },
      { id: assistantMsgId, role: 'assistant', content: '', timestamp: new Date().toISOString(), thinking: null, thinkingUnavailable: false, guardrailIntervened: false },
    ]);
    setInputText('');
    setLoading(true);
    setError(null);

    const abort = new AbortController();
    abortRef.current = abort;

    // Local accumulator so we can inspect the full answer at [DONE] without
    // depending on async state updates.
    let answer = '';

    const patchAssistant = (fn: (m: ChatMessage) => ChatMessage) =>
      setMessages((prev) => prev.map((m) => (m.id === assistantMsgId ? fn(m) : m)));

    const dropAssistant = () =>
      setMessages((prev) => prev.filter((m) => m.id !== assistantMsgId));

    await agentChatStream(
      { sessionId, message: trimmed, filters },
      {
        signal: abort.signal,
        onThinking: (chunk) =>
          patchAssistant((m) => ({ ...m, thinking: (m.thinking ?? '') + chunk })),
        onText: (chunk) => {
          answer += chunk;
          patchAssistant((m) => ({ ...m, content: m.content + chunk }));
        },
        onThinkingUnavailable: () =>
          patchAssistant((m) => ({ ...m, thinkingUnavailable: true })),
        onGuardrailIntervened: () =>
          patchAssistant((m) => ({ ...m, guardrailIntervened: true })),
        onDone: (sid) => {
          if (sid) setSessionId(sid);
          // Preserve the existing "insufficient data" UX: surface as an error
          // banner rather than a chat bubble.
          if (answer.toLowerCase().includes('not enough usage data')) {
            setError(answer);
            dropAssistant();
          }
        },
        onError: (err) => {
          setError(`${err.code}: ${err.message}`);
          // Remove the assistant bubble only if nothing streamed into it.
          setMessages((prev) =>
            prev.filter((m) => !(m.id === assistantMsgId && m.content === '' && !m.thinking))
          );
        },
      }
    );

    setLoading(false);
    abortRef.current = null;
  };

  const handleStop = () => {
    // Cancel the in-flight stream and finalize whatever partial content arrived.
    abortRef.current?.abort();
    abortRef.current = null;
    // Drop an assistant bubble that never received any content.
    setMessages((prev) => prev.filter((m) => !(m.role === 'assistant' && m.content === '' && !m.thinking)));
    setLoading(false);
  };

  const handleNewChat = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setMessages([]);
    setSessionId(null);
    setInputText('');
    setError(null);
    setLoading(false);
  };

  return (
    <div data-testid="agent-chat-container" style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      {/* Deployment disclaimer — Agent Chat captures user-authored prompt
          text that may be persisted + encrypted at rest depending on the
          Prompt History toggle, so no-warranty / no-liability language sits
          above the composer every time this tab is opened. */}
      <DeploymentDisclaimer
        heading="Agent Chat — your prompts may be persisted"
        context={
          'Prompts you send here are forwarded to Amazon Bedrock. When Prompt History is ' +
          'enabled in Admin → Settings, both your prompt and the model response are stored ' +
          'in the chat_messages table (encrypted at rest when a key is configured). Avoid ' +
          'submitting production credentials or data you are not authorized to share.'
        }
      />

    <div data-testid="agent-chat" style={{ display: 'flex', flexDirection: 'column', height: '600px', border: '1px solid #e0e0e0', borderRadius: '8px', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0.75rem 1rem', borderBottom: '1px solid #e0e0e0', background: '#f9f9f9' }}>
        <h3 style={{ margin: 0, fontSize: '1rem', color: '#16191f' }}>AI Agent Chat</h3>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <button
            onClick={handleNewChat}
            data-testid="new-chat-button"
            style={{ padding: '0.375rem 0.75rem', border: '1px solid #ccc', borderRadius: '4px', background: '#fff', cursor: 'pointer', fontSize: '0.8125rem', color: '#333' }}
          >
            New Chat
          </button>
        </div>
      </div>

      {/* Messages area */}
      <div data-testid="messages-area" style={{ flex: 1, overflowY: 'auto', padding: '1rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
        {messages.length === 0 && !loading && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '1rem', marginTop: '1.5rem', padding: '0 1rem' }}>
            <div style={{ color: '#888', fontSize: '0.875rem' }}>
              Ask a question about your Bedrock usage data, or try one of these:
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', justifyContent: 'center', maxWidth: '600px' }}>
              {SUGGESTED_PROMPTS.map((prompt, idx) => (
                <button
                  key={idx}
                  data-testid={`suggested-prompt-${idx}`}
                  onClick={() => {
                    setInputText(prompt.text);
                  }}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.375rem',
                    padding: '0.5rem 0.75rem',
                    border: '1px solid #e0e0e0',
                    borderRadius: '8px',
                    background: '#f9f9f9',
                    cursor: 'pointer',
                    fontSize: '0.8125rem',
                    color: '#333',
                    transition: 'all 0.15s',
                    textAlign: 'left',
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLButtonElement).style.borderColor = '#0073bb';
                    (e.currentTarget as HTMLButtonElement).style.background = '#f0f7ff';
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLButtonElement).style.borderColor = '#e0e0e0';
                    (e.currentTarget as HTMLButtonElement).style.background = '#f9f9f9';
                  }}
                >
                  <span>{prompt.icon}</span>
                  <span>{prompt.text}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((msg, idx) => (
          <div key={msg.id} style={{ alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start', maxWidth: '75%' }}>
            {/* Thinking block — rendered above assistant message */}
            {msg.role === 'assistant' && msg.thinking && (
              <details
                data-testid={`thinking-block-${idx}`}
                style={{
                  marginBottom: '0.5rem',
                  padding: '0.5rem 0.75rem',
                  borderRadius: '8px',
                  background: '#f7f5ff',
                  border: '1px solid #e0daf5',
                  fontSize: '0.8125rem',
                  color: '#666',
                }}
              >
                <summary style={{ cursor: 'pointer', fontWeight: 600, color: '#7c6bb5', userSelect: 'none' }}>
                  🧠 Thinking
                </summary>
                <div
                  data-testid={`thinking-content-${idx}`}
                  style={{ marginTop: '0.375rem', paddingLeft: '0.5rem', whiteSpace: 'pre-wrap', lineHeight: '1.4' }}
                >
                  {msg.thinking}
                </div>
              </details>
            )}
            {/* Thinking unavailable notice */}
            {msg.role === 'assistant' && msg.thinkingUnavailable && (
              <div
                data-testid={`thinking-unavailable-${idx}`}
                style={{
                  marginBottom: '0.375rem',
                  padding: '0.375rem 0.625rem',
                  borderRadius: '6px',
                  background: '#fffbeb',
                  border: '1px solid #fde68a',
                  fontSize: '0.75rem',
                  color: '#92400e',
                }}
              >
                Reasoning was unavailable for this response
              </div>
            )}
            {/* Guardrail intervened notice */}
            {msg.role === 'assistant' && msg.guardrailIntervened && (
              <div
                data-testid={`guardrail-intervened-${idx}`}
                style={{
                  marginBottom: '0.375rem',
                  padding: '0.375rem 0.625rem',
                  borderRadius: '6px',
                  background: '#fef2f2',
                  border: '1px solid #fecaca',
                  fontSize: '0.75rem',
                  color: '#991b1b',
                }}
              >
                🛡 This response was modified by the dashboard&apos;s guardrail policy
              </div>
            )}
            {/* Message bubble. Assistant content renders as markdown; user text
                stays plain (pre-wrap) to avoid interpreting user input as markup. */}
            <div
              data-testid={`message-${msg.role}-${idx}`}
              style={{
                padding: '0.625rem 0.875rem',
                borderRadius: '12px',
                background: msg.role === 'user' ? '#0073bb' : '#f0f0f0',
                color: msg.role === 'user' ? '#fff' : '#333',
                fontSize: '0.875rem',
                lineHeight: '1.4',
                whiteSpace: msg.role === 'user' ? 'pre-wrap' : 'normal',
                wordBreak: 'break-word',
              }}
            >
              {msg.role === 'assistant' ? (
                <AssistantMarkdown content={msg.content} />
              ) : (
                msg.content
              )}
            </div>
          </div>
        ))}
        {loading &&
          !messages[messages.length - 1]?.content &&
          !messages[messages.length - 1]?.thinking && (
            <div data-testid="chat-loading" style={{ alignSelf: 'flex-start', color: '#888', fontSize: '0.875rem', fontStyle: 'italic' }}>
              Thinking...
            </div>
          )}
        <div ref={messagesEndRef} />
      </div>

      {/* Error display */}
      {error && (
        <div data-testid="chat-error" style={{ padding: '0.5rem 1rem', background: '#fef2f2', color: '#b91c1c', fontSize: '0.8125rem', borderTop: '1px solid #fecaca' }}>
          {error}
        </div>
      )}

      {/* Input area */}
      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: '0.5rem', padding: '0.75rem 1rem', borderTop: '1px solid #e0e0e0', background: '#f9f9f9' }}>
        <input
          type="text"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          placeholder="Ask about your Bedrock usage..."
          data-testid="chat-input"
          disabled={loading}
          style={{ flex: 1, padding: '0.5rem 0.75rem', border: '1px solid #ccc', borderRadius: '4px', fontSize: '0.875rem', outline: 'none' }}
        />
        {loading ? (
          <button
            type="button"
            onClick={handleStop}
            data-testid="stop-button"
            style={{
              padding: '0.5rem 1rem',
              border: '1px solid #b91c1c',
              borderRadius: '4px',
              background: '#fff',
              color: '#b91c1c',
              cursor: 'pointer',
              fontSize: '0.875rem',
              fontWeight: 600,
            }}
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            data-testid="send-button"
            disabled={!inputText.trim()}
            style={{
              padding: '0.5rem 1rem',
              border: 'none',
              borderRadius: '4px',
              background: !inputText.trim() ? '#ccc' : '#0073bb',
              color: '#fff',
              cursor: !inputText.trim() ? 'default' : 'pointer',
              fontSize: '0.875rem',
              fontWeight: 600,
            }}
          >
            Send
          </button>
        )}
      </form>
    </div>
    </div>
  );
};

export default AgentChat;
