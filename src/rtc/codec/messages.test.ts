import { describe, expect, it } from 'vitest';
import {
  AGENT_BRIEF,
  MESSAGE_TYPE,
  briefStateToAction,
  extractFunctionCall,
  extractSubtitle,
  parseSemanticMessage,
} from './messages';
import { string2tlv } from './tlv';

const encode = (type: string, payload: unknown) => string2tlv(JSON.stringify(payload), type);

describe('semantic message parsing', () => {
  it('parses BRIEF buffer into typed message', () => {
    const parsed = parseSemanticMessage(
      encode('conv', { Stage: { Code: AGENT_BRIEF.SPEAKING } })
    );
    expect(parsed).toEqual({
      type: MESSAGE_TYPE.BRIEF,
      payload: { Stage: { Code: AGENT_BRIEF.SPEAKING } },
    });
  });

  it('maps every AGENT_BRIEF code to a domain action', () => {
    expect(briefStateToAction(AGENT_BRIEF.THINKING)).toBe('thinking');
    expect(briefStateToAction(AGENT_BRIEF.SPEAKING)).toBe('speaking');
    expect(briefStateToAction(AGENT_BRIEF.FINISHED)).toBe('finished');
    expect(briefStateToAction(AGENT_BRIEF.INTERRUPTED)).toBe('interrupted');
    expect(briefStateToAction(AGENT_BRIEF.LISTENING)).toBeNull();
    expect(briefStateToAction(AGENT_BRIEF.UNKNOWN)).toBeNull();
  });

  it('extracts first subtitle entry', () => {
    const payload = {
      data: [{ text: '你好', definite: true, userId: 'user1', paragraph: true }],
    };
    expect(extractSubtitle(payload)).toEqual({
      text: '你好',
      user: 'user1',
      paragraph: true,
      definite: true,
    });
  });

  it('returns null subtitle when data is empty', () => {
    expect(extractSubtitle({ data: [] })).toBeNull();
  });

  it('extracts function call id and name', () => {
    const payload = { tool_calls: [{ id: 'call_1', function: { name: 'getcurrentweather' } }] };
    expect(extractFunctionCall(payload)).toEqual({ id: 'call_1', name: 'getcurrentweather' });
  });

  it('returns null function call when tool_calls missing', () => {
    expect(extractFunctionCall({})).toBeNull();
  });

  it('returns null on unknown message type', () => {
    expect(parseSemanticMessage(encode('xxxx', {}))).toBeNull();
  });

  it('returns null on malformed binary payload', () => {
    expect(parseSemanticMessage(new ArrayBuffer(8))).toBeNull();
  });
});
