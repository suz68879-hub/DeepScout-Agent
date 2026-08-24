import { describe, expect, it } from 'vitest';

import { E2E_RTC_CONFIG, E2E_SCENE_CONFIG, E2E_SCENE_ID, E2E_SCRIPT } from './e2eMock';

describe('e2eMock fixture', () => {
  it('场景与 RTC 配置形状完整（SceneConfig / RTCConfig 字段齐全）', () => {
    expect(E2E_SCENE_ID).toBe('e2e-mock');
    expect(E2E_SCENE_CONFIG.botName).toBe('懂小智');
    expect(E2E_SCENE_CONFIG.isAvatarScene).toBe(false);
    expect(E2E_RTC_CONFIG.AppId).toBe('e2e-mock');
    expect(E2E_RTC_CONFIG.RoomId).toBe('e2e-room');
    expect(E2E_RTC_CONFIG.UserId).toBe('e2e-user');
  });

  it('脚本按自我介绍、技术基础、项目深挖推进', () => {
    const technicalIndex = E2E_SCRIPT.findIndex(line => line.text.includes('Redis'));
    const projectIndex = E2E_SCRIPT.findIndex(line => line.text.includes('库存是怎么扣的'));
    expect(technicalIndex).toBeGreaterThan(-1);
    expect(projectIndex).toBeGreaterThan(technicalIndex);
  });
});
