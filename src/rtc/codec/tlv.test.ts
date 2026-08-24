import { describe, expect, it } from 'vitest';
import { string2tlv, tlv2String } from './tlv';

describe('tlv codec', () => {
  it('encodes type as first 4 bytes and length as big-endian uint32', () => {
    const buffer = string2tlv('hello', 'ctrl');
    const bytes = new Uint8Array(buffer);
    expect([...bytes.slice(0, 4)]).toEqual([0x63, 0x74, 0x72, 0x6c]); // 'ctrl'
    expect([...bytes.slice(4, 8)]).toEqual([0, 0, 0, 5]);
    expect(new TextDecoder().decode(bytes.slice(8))).toBe('hello');
  });

  it('round-trips utf-8 chinese text with byte length not char length', () => {
    const text = '你好，面试官';
    const buffer = string2tlv(text, 'subv');
    const bytes = new Uint8Array(buffer);
    const byteLength = new TextEncoder().encode(text).byteLength;
    expect([...bytes.slice(4, 8)]).toEqual([
      (byteLength >> 24) & 0xff,
      (byteLength >> 16) & 0xff,
      (byteLength >> 8) & 0xff,
      byteLength & 0xff,
    ]);
    expect(tlv2String(buffer)).toEqual({ type: 'subv', value: text });
  });

  it('round-trips an empty string', () => {
    expect(tlv2String(string2tlv('', 'conv'))).toEqual({ type: 'conv', value: '' });
  });
});
