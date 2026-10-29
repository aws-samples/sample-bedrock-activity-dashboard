import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import AgentChat from './AgentChat';
import type { DashboardFilters } from '../types';
import type { AgentChatStreamHandlers } from '../api/client';

vi.mock('../api/client', () => ({
  agentChatStream: vi.fn(),
}));

import { agentChatStream } from '../api/client';

const mockedStream = vi.mocked(agentChatStream);

const mockFilters: DashboardFilters = {
  dateRange: { start: '2025-01-01', end: '2025-01-07' },
  userIdentities: [],
  teamId: null,
  sourceApplication: null,
};

/** Build a stream mock that emits the given handler calls, then resolves. */
function emitting(run: (h: AgentChatStreamHandlers) => void) {
  return (_req: unknown, h: AgentChatStreamHandlers): Promise<void> => {
    run(h);
    return Promise.resolve();
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AgentChat', () => {
  it('renders input and send button', () => {
    render(<AgentChat filters={mockFilters} />);
    expect(screen.getByTestId('chat-input')).toBeTruthy();
    expect(screen.getByTestId('send-button')).toBeTruthy();
  });

  it('renders new chat button', () => {
    render(<AgentChat filters={mockFilters} />);
    expect(screen.getByTestId('new-chat-button')).toBeTruthy();
  });

  it('shows placeholder text when no messages', () => {
    render(<AgentChat filters={mockFilters} />);
    expect(screen.getByText(/Ask a question about your Bedrock usage data/)).toBeTruthy();
  });

  it('disables send button when input is empty', () => {
    render(<AgentChat filters={mockFilters} />);
    const sendBtn = screen.getByTestId('send-button') as HTMLButtonElement;
    expect(sendBtn.disabled).toBe(true);
  });

  // --- Suggested prompts (#4) ---

  it('renders the two new data-backed suggested prompts and drops the unsupported ones', () => {
    render(<AgentChat filters={mockFilters} />);
    expect(screen.getByText('Which source applications drive the most usage?')).toBeTruthy();
    expect(screen.getByText('Break down my input vs output vs cache token usage')).toBeTruthy();
    // The old unsupported prompts must be gone.
    expect(screen.queryByText('Are there any usage anomalies or spikes?')).toBeNull();
    expect(screen.queryByText('Summarize the security alerts detected this week')).toBeNull();
  });

  // --- Streaming behavior (#2) ---

  it('streams text chunks live into the assistant bubble', async () => {
    mockedStream.mockImplementation(
      emitting((h) => {
        h.onText?.('Your total ');
        h.onText?.('token usage is 50,000.');
        h.onDone?.('session-1');
      })
    );

    render(<AgentChat filters={mockFilters} />);
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'What is my token usage?' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      expect(screen.getByText('What is my token usage?')).toBeTruthy();
    });
    await waitFor(() => {
      expect(screen.getByText('Your total token usage is 50,000.')).toBeTruthy();
    });

    expect(mockedStream).toHaveBeenCalledWith(
      { sessionId: null, message: 'What is my token usage?', filters: mockFilters },
      expect.objectContaining({ onText: expect.any(Function), signal: expect.any(AbortSignal) })
    );
  });

  it('streams thinking chunks live into the thinking block', async () => {
    mockedStream.mockImplementation(
      emitting((h) => {
        h.onThinking?.('Step 1... ');
        h.onThinking?.('Step 2...');
        h.onText?.('The answer is 42.');
        h.onDone?.('session-1');
      })
    );

    render(<AgentChat filters={mockFilters} />);
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Question' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      expect(screen.getByTestId('thinking-content-1').textContent).toBe('Step 1... Step 2...');
      expect(screen.getByText('The answer is 42.')).toBeTruthy();
    });
  });

  it('renders assistant markdown (bold + list)', async () => {
    mockedStream.mockImplementation(
      emitting((h) => {
        h.onText?.('This is **bold** text\n\n- one\n- two');
        h.onDone?.('session-1');
      })
    );

    render(<AgentChat filters={mockFilters} />);
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Question' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      const bold = screen.getByText('bold');
      expect(bold.tagName).toBe('STRONG');
      expect(screen.getByText('one').tagName).toBe('LI');
      expect(screen.getByText('two').tagName).toBe('LI');
    });
  });

  it('captures sessionId from [DONE] and reuses it on the next turn', async () => {
    mockedStream.mockImplementationOnce(
      emitting((h) => {
        h.onText?.('First response');
        h.onDone?.('session-abc');
      })
    );

    render(<AgentChat filters={mockFilters} />);
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'First' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      expect(screen.getByText('First response')).toBeTruthy();
    });

    mockedStream.mockImplementationOnce(
      emitting((h) => {
        h.onText?.('Second response');
        h.onDone?.('session-abc');
      })
    );

    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Second' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      expect(screen.getByText('Second response')).toBeTruthy();
    });

    expect(mockedStream).toHaveBeenLastCalledWith(
      { sessionId: 'session-abc', message: 'Second', filters: mockFilters },
      expect.anything()
    );
  });

  it('surfaces stream errors in the error UI', async () => {
    mockedStream.mockImplementation(
      emitting((h) => {
        h.onError?.({ code: 'BEDROCK_ERROR', message: 'Model unavailable' });
      })
    );

    render(<AgentChat filters={mockFilters} />);
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Hello' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      expect(screen.getByTestId('chat-error')).toBeTruthy();
      expect(screen.getByText('BEDROCK_ERROR: Model unavailable')).toBeTruthy();
    });
  });

  it('shows a Stop button while loading and aborts the stream when clicked', async () => {
    let captured: AgentChatStreamHandlers | null = null;
    mockedStream.mockImplementation((_req, h) => {
      captured = h;
      return new Promise<void>(() => {}); // never resolves — stays "loading"
    });

    render(<AgentChat filters={mockFilters} />);
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Hello' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      expect(screen.getByTestId('stop-button')).toBeTruthy();
    });

    expect(captured).not.toBeNull();
    expect(captured!.signal?.aborted).toBe(false);

    fireEvent.click(screen.getByTestId('stop-button'));

    expect(captured!.signal?.aborted).toBe(true);
    // Loading finished → Send button returns.
    await waitFor(() => {
      expect(screen.getByTestId('send-button')).toBeTruthy();
    });
  });

  it('shows loading indicator until the first chunk arrives', async () => {
    mockedStream.mockImplementation(() => new Promise<void>(() => {}));

    render(<AgentChat filters={mockFilters} />);
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Hello' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      expect(screen.getByTestId('chat-loading')).toBeTruthy();
      expect(screen.getByText('Thinking...')).toBeTruthy();
    });
    expect((screen.getByTestId('chat-input') as HTMLInputElement).disabled).toBe(true);
  });

  it('displays the thinkingUnavailable notice', async () => {
    mockedStream.mockImplementation(
      emitting((h) => {
        h.onThinkingUnavailable?.();
        h.onText?.('Fallback response.');
        h.onDone?.('session-1');
      })
    );

    render(<AgentChat filters={mockFilters} />);
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Question' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      expect(screen.getByTestId('thinking-unavailable-1')).toBeTruthy();
      expect(screen.getByText('Reasoning was unavailable for this response')).toBeTruthy();
      expect(screen.getByText('Fallback response.')).toBeTruthy();
    });
  });

  it('displays the guardrail-intervened notice when the managed guardrail blocked/anonymized content', async () => {
    mockedStream.mockImplementation(
      emitting((h) => {
        h.onText?.("Sorry, I can't help with that.");
        h.onGuardrailIntervened?.();
        h.onDone?.('session-1');
      })
    );

    render(<AgentChat filters={mockFilters} />);
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'My SSN is 123-45-6789' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      expect(screen.getByTestId('guardrail-intervened-1')).toBeTruthy();
      expect(screen.getByText(/This response was modified by the dashboard's guardrail policy/)).toBeTruthy();
    });
  });

  it('does not show the guardrail-intervened notice on a normal response', async () => {
    mockedStream.mockImplementation(
      emitting((h) => {
        h.onText?.('Here is your answer.');
        h.onDone?.('session-1');
      })
    );

    render(<AgentChat filters={mockFilters} />);
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Question' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      expect(screen.getByText('Here is your answer.')).toBeTruthy();
    });
    expect(screen.queryByTestId('guardrail-intervened-1')).toBeNull();
  });

  it('surfaces the insufficient-data response as an error banner', async () => {
    mockedStream.mockImplementation(
      emitting((h) => {
        h.onText?.('Not enough usage data to answer this question');
        h.onDone?.('session-1');
      })
    );

    render(<AgentChat filters={mockFilters} />);
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'What is my usage?' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      expect(screen.getByTestId('chat-error').textContent).toBe('Not enough usage data to answer this question');
    });
  });

  it('New Chat resets the conversation', async () => {
    mockedStream.mockImplementation(
      emitting((h) => {
        h.onText?.('Response 1');
        h.onDone?.('session-1');
      })
    );

    render(<AgentChat filters={mockFilters} />);
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Hello' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      expect(screen.getByText('Response 1')).toBeTruthy();
    });

    fireEvent.click(screen.getByTestId('new-chat-button'));

    expect(screen.queryByText('Hello')).toBeNull();
    expect(screen.queryByText('Response 1')).toBeNull();
    expect(screen.getByText(/Ask a question about your Bedrock usage data/)).toBeTruthy();
  });

  it('clears the input immediately after sending', async () => {
    mockedStream.mockImplementation(
      emitting((h) => {
        h.onText?.('Response');
        h.onDone?.('session-1');
      })
    );

    render(<AgentChat filters={mockFilters} />);
    const input = screen.getByTestId('chat-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Hello' } });
    fireEvent.click(screen.getByTestId('send-button'));

    expect(input.value).toBe('');
  });

  // --- Thinking toggle removed ---

  it('does not render the Show Thinking toggle in the header', () => {
    render(<AgentChat filters={mockFilters} />);
    expect(screen.queryByTestId('thinking-toggle')).toBeNull();
    expect(screen.queryByTestId('thinking-toggle-input')).toBeNull();
    expect(screen.queryByText('Show Thinking')).toBeNull();
  });

  it('renders streamed thinking content without any toggle needing to be enabled', async () => {
    mockedStream.mockImplementation(
      emitting((h) => {
        h.onThinking?.('Step by step reasoning...');
        h.onText?.('The answer is 42.');
        h.onDone?.('session-1');
      })
    );

    render(<AgentChat filters={mockFilters} />);
    fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'Question' } });
    fireEvent.click(screen.getByTestId('send-button'));

    await waitFor(() => {
      expect(screen.getByTestId('thinking-block-1')).toBeTruthy();
      expect(screen.getByTestId('thinking-content-1').textContent).toBe('Step by step reasoning...');
      expect(screen.getByText('The answer is 42.')).toBeTruthy();
    });
  });
});
