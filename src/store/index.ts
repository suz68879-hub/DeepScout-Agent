/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

import { configureStore, createListenerMiddleware } from '@reduxjs/toolkit';
import roomSlice, { RoomState, updateRTCConfig } from './slices/room';
import deviceSlice, { DeviceState } from './slices/device';

export interface RootState {
  room: RoomState;
  device: DeviceState;
}

/**
 * reducer 纯化（spec §4.3 架构纠偏）：
 * 官方 updateRTCConfig 在 reducer 内直接改原官方单例的 basicInfo（副作用）。
 * 改为 listener middleware 监听 action 后执行副作用，reducer 只更新 state。
 */
const listenerMiddleware = createListenerMiddleware();

listenerMiddleware.startListening({
  actionCreator: updateRTCConfig,
  effect: async (action, listenerApi) => {
    const state = listenerApi.getState() as RootState;
    const config = action.payload[state.room.scene];
    const { rtcEngine } = await import('@/rtc/RtcEngine');
    rtcEngine.configure(config);
  },
});

const store = configureStore({
  reducer: {
    room: roomSlice,
    device: deviceSlice,
  },
  middleware: (getDefaultMiddleware) =>
    getDefaultMiddleware({
      serializableCheck: false,
    }).prepend(listenerMiddleware.middleware),
});

export default store;
