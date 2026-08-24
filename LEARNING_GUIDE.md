# 火山引擎 AIGC-RTC 实时对话 Demo — 项目学习文档

> **适用场景**: 面试准备、项目学习、了解 RTC + AI 实时对话架构  
> **原作者**: 北京火山引擎科技有限公司（BSD-3-Clause 许可证）  
> **Demo 版本**: 1.6.0

---

## 目录

1. [项目概览](#1-项目概览)
2. [架构全景图](#2-架构全景图)
3. [技术栈分析](#3-技术栈分析)
4. [逐层递进：代码剖析](#4-逐层递进代码剖析)
   - [第一层：项目启动与路由](#第一层项目启动与路由)
   - [第二层：服务端 — Node.js 网关](#第二层服务端--nodejs-网关)
   - [第三层：前端 API 层 — 请求抽象](#第三层前端-api-层--请求抽象)
   - [第四层：RTC 客户端 — 音视频核心](#第四层rtc-客户端--音视频核心)
   - [第五层：事件监听与语义解析](#第五层事件监听与语义解析)
   - [第六层：Redux 状态管理](#第六层redux-状态管理)
   - [第七层：UI 页面架构](#第七层ui-页面架构)
   - [第八层：辅助服务 — Python 实现](#第八层辅助服务--python-实现)
5. [数据流全景：一次完整的 AI 对话](#5-数据流全景一次完整的-ai-对话)
6. [关键设计模式与亮点](#6-关键设计模式与亮点)
7. [面试高频问题](#7-面试高频问题)
8. [项目启动指南](#8-项目启动指南)
9. [提交到码云 Git 仓库](#9-提交到码云-git-仓库)

---

## 1. 项目概览

### 1.1 这是什么？

这是一个**基于火山引擎 RTC（实时音视频通信）+ 大模型（LLM）+ ASR（语音识别）+ TTS（语音合成）的 AI 实时对话应用**。你可以像打电话一样跟 AI 对话：你说话 → AI 听懂 → 大模型思考 → AI 用语音回答你。

支持的模式：
- 🎤 **纯语音对话**（默认）
- 📷 **视觉对话**（开启摄像头，AI 能看到你）
- 🖥️ **屏幕共享对话**（AI 看你屏幕内容）
- 🧑 **数字人对话**（3D 虚拟人形象）

### 1.2 核心业务流程

```
浏览器麦克风 ──→ 火山 RTC SDK ──→ 火山云端服务 ──→ ASR 语音转文字
                                                       ↓
浏览器扬声器 ←── 火山 RTC SDK ←── 火山云端服务 ←── LLM 大模型生成回复
                                                       ↓
                                                   TTS 文字转语音
```

### 1.3 目录结构速览

```
ark_aigc_demo-main/
├── rag_llm_server/         # 后端（FastAPI + LangGraph 7 Agent + RAG，端口 3001）
│   ├── main.py             # FastAPI 装配、CORS 与 lifespan
│   ├── api/rtc.py          # /getScenes、/proxy、/api/chat_callback
│   ├── services/
│   │   ├── rtc_service.py  # RTC Token、场景与 OpenAPI 调用
│   │   ├── llm_service.py  # LLM 调用服务
│   │   ├── rag_service.py  # 知识库检索服务
│   │   └── token_build.py  # Token 生成
│   └── config.py           # 配置管理
├── src/                    # React 前端
│   ├── index.tsx           # ReactDOM 入口，挂载 Redux Provider
│   ├── App.tsx             # React Router 路由定义
│   ├── app/               # API 抽象层（请求封装）
│   ├── config/            # 前端配置
│   ├── lib/               # 核心库
│   │   ├── RtcClient.ts   # RTC 客户端单例（封装所有音视频操作）
│   │   ├── listenerHooks.ts # RTC 事件 → Redux 的桥接 Hook
│   │   └── useCommon.ts   # 通用 Hook（进房/离房/设备管理）
│   ├── store/             # Redux Toolkit 状态管理
│   │   └── slices/
│   │       ├── room.ts    # 房间状态（最核心的 slice，300+ 行）
│   │       └── device.ts  # 设备状态
│   ├── pages/             # 页面组件
│   │   ├── MainPage/      # 主页面
│   │   │   ├── MainArea/  # 核心区域
│   │   │   │   ├── Antechamber/ # "等待室"（进房前）
│   │   │   │   └── Room/       # "通话室"（进房后）
│   │   │   └── Menu/           # 侧边栏菜单
│   │   └── Mobile/             # 移动端适配
│   ├── components/        # 通用组件
│   └── utils/             # 工具函数（TLV 编解码、Logger）
├── public/                # 静态资源
├── craco.config.js        # Webpack 配置（craco = CRA 的定制层）
├── package.json           # 前端依赖（React 18 + RTC SDK + Redux Toolkit）
└── tsconfig.json          # TypeScript 配置
```

---

## 2. 架构全景图

```
                     ┌─────────────────────────────────────┐
                     │        浏览器 (React SPA)             │
                     │                                     │
 ┌───────────┐      │  ┌─────────┐    ┌─────────────────┐ │
 │ 用户麦克风 │ ───→ │RTC SDK  │←──→│  Redux Store    │ │
 │ 用户扬声器 │ ←─── │(volceng │    │  room / device  │ │
 │ 用户摄像头 │      │ ine/rtc)│    └─────────────────┘ │
 └───────────┘      └────┬────┘            ↕              │
                         │            React 组件树        │
                         │    (Antechamber / Room / Menu) │
                         │                               │
 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ┼ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│─
                         │   HTTP (localhost:3001)        │
 ┌────────────────┐      │                               │
 │  Node/Python   │ ←───┘                               │
 │  Server :3001  │
 │                │
 │ /proxy         │  → 调用火山 OpenAPI（ak/sk 签名）     │
 │ /getScenes     │  → 返回场景配置 + 自动签发 RTC Token  │
 └───────┬────────┘
         │ HTTPS
         ▼
 ┌──────────────────────┐
 │ 火山引擎云端服务       │
 │ rtc.volcengineapi.com│
 │                      │
 │ ASR → LLM → TTS     │
 │ 语音识别 → 大模型 → 语音合成 │
 └──────────────────────┘
```

**核心理解**：前端不直接调火山的 OpenAPI（因为有 AK/SK 鉴权），而是通过本地的 Node/Python 服务作为 **API Proxy**，服务端用 AK/SK 签名后转发请求。

---

## 3. 技术栈分析

| 层级 | 技术 | 说明 |
|------|------|------|
| **前端框架** | React 18 + TypeScript | 函数式组件 + Hooks |
| **状态管理** | Redux Toolkit | 两个 slice: `room` + `device` |
| **UI 库** | @arco-design/web-react | 字节跳动旗下的组件库 |
| **路由** | react-router-dom v6 | BrowserRouter |
| **样式** | Less + CSS Modules | 每个组件配套 `.module.less` |
| **构建工具** | craco (CRA 定制版) | 支持 Less、路径别名 `@/` |
| **RTC SDK** | @volcengine/rtc ~4.66.20 | 火山引擎 WebRTC SDK |
| **Node 后端** | Koa 2 | 轻量级 HTTP 框架 |
| **Python 后端** | FastAPI + uvicorn | ASGI 异步框架 |
| **HTTP 客户端** | node-fetch / httpx | 服务端调用火山 API |
| **代码规范** | ESLint + Prettier + Stylelint | 完整的 Lint 工具链 |

---

## 4. 逐层递进：代码剖析

### 第一层：项目启动与路由

#### 入口文件：`src/index.tsx`

```typescript
// 1. 创建 React 根节点
const root = ReactDOM.createRoot(document.getElementById('root'));
// 2. 包裹 Redux Provider（全局状态注入）
root.render(
  <Provider store={store}>
    <App />
  </Provider>
);
```

**设计要点**：Redux Provider 在最外层，所有子组件都能通过 `useSelector`/`useDispatch` 访问全局状态。

#### 路由定义：`src/App.tsx`

```typescript
function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/">
          <Route index element={<MainPage />} />
          <Route path="/*" element={<MainPage />} />  {/* 所有路径都指向 MainPage */}
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
```

**设计要点**：单页应用，只有一条路由，所有交互通过**状态切换**（进房/离房）而非页面跳转实现。

#### 主页面：`src/pages/MainPage/index.tsx`

```typescript
export default function () {
  // 1. 启动时从服务端获取场景配置
  const getScenes = async () => {
    const { scenes } = await Apis.Basic.getScenes();
    dispatch(updateScene(scenes[0].scene.id));
    dispatch(updateSceneConfig(...));
    dispatch(updateRTCConfig(...));  // 同步更新 RtcClient.basicInfo
  };

  useEffect(() => {
    getScenes();
    // 页面切到后台时自动离房（非本地调试时生效）
    const handler = () => {
      if (document.visibilityState === 'hidden') leaveRoom();
    };
    document.addEventListener('visibilitychange', handler);
  }, []);

  return (
    <ResizeWrapper>
      <Header />
      <div className={styles.main}>
        <MainArea />     {/* 左侧核心区：Antechamber 或 Room */}
        <Menu />         {/* 右侧菜单栏 */}
      </div>
    </ResizeWrapper>
  );
}
```

**关键设计**：
- 页面加载时立刻调用 `/getScenes`，获取所有场景配置
- `updateRTCConfig` 不仅存 Redux，还同步更新 `RtcClient.basicInfo`
- `visibilitychange` 事件：切后台自动离房，防止资源泄露

#### Antechamber → Room 状态切换：`src/pages/MainPage/MainArea/index.tsx`

```typescript
function MainArea() {
  const isJoined = useSelector((state) => state.room.isJoined);
  return isJoined ? <Room /> : <Antechamber />;
}
```

这是整个 UI 的核心状态机：`isJoined === false` → 显示"等待室"，点击「呼叫」按钮 → `isJoined === true` → 切换到"通话室"。

---

### 第二层：服务端 — Node.js 网关（官方原版，已删除）

> 原 `Server/`（Node.js）与 `server_python/` 已在改造中删除，本节为历史讲解，仅供参考；
> 当前唯一后端为 `rag_llm_server/`（FastAPI + LangGraph，入口 `main.py`）。

#### `Server/app.js` 核心逻辑

服务端提供两个 API：

**① `POST /getScenes` — 获取场景配置 + 自动签发 Token**

```javascript
wrapper({
  ctx, apiName: 'getScenes',
  logic: () => {
    const scenes = Object.keys(Scenes).map((scene) => {
      const { SceneConfig, RTCConfig, VoiceChat } = Scenes[scene];
      // 如果没配 Token/UserId/RoomId，自动生成
      if (AppId && (!Token || !UserId || !RoomId)) {
        RTCConfig.RoomId = VoiceChat.RoomId = RoomId || uuid.v4();
        RTCConfig.UserId = VoiceChat.AgentConfig.TargetUserId[0] = UserId || uuid.v4();
        // 用 AppKey 签发 Token（24 小时有效期）
        const key = new AccessToken(AppId, AppKey, RoomId, UserId);
        key.addPrivilege(PrivSubscribeStream, 0);
        key.addPrivilege(PrivPublishStream, 0);
        key.expireTime(Math.floor(new Date() / 1000) + 24 * 3600);
        RTCConfig.Token = key.serialize();
      }
      // 删除敏感的 AppKey 不传给前端
      delete RTCConfig.AppKey;
      return { scene: SceneConfig, rtc: RTCConfig };
    });
  }
});
```

**面试要点**：
- Token 是用 HMAC-SHA256 + AppKey 签名的，服务端签发，前端只管使用
- 为安全起见，`AppKey` 在返回前端前被删除
- 每个场景支持独立的 RTC 配置（多场景架构）

**② `POST /proxy` — 代理火山 OpenAPI 请求**

```javascript
wrapper({
  apiName: 'proxy',
  logic: async () => {
    const { Action } = ctx.query;
    const { SceneID } = ctx.request.body;
    const { VoiceChat, AccountConfig } = Scenes[SceneID];

    // 根据 Action 构造请求体
    let body = {};
    if (Action === 'StartVoiceChat') body = VoiceChat;
    else if (Action === 'StopVoiceChat')
      body = { AppId, RoomId, TaskId };

    // 火山 AK/SK 签名
    const signer = new Signer(openApiRequestData, "rtc");
    signer.addAuthorization(AccountConfig);

    // 转发到火山 OpenAPI
    const result = await fetch(
      `https://rtc.volcengineapi.com?Action=${Action}&Version=${Version}`,
      { method: 'POST', headers, body }
    );
    return result.json();
  }
});
```

**面试要点**：
- 这是一个典型的 **API 网关 / BFF（Backend For Frontend）模式**：前端不能直接持有 AK/SK，由后端代理签名
- `Action` 参数决定了调哪个 OpenAPI（`StartVoiceChat`、`StopVoiceChat`）
- 场景配置和账号配置分离：`Custom.json` 中 `AccountConfig`（密钥）和 `VoiceChat`（业务参数）独立

#### `Server/token.js` — RTC Token 的密码学实现

这是整个项目中最 "硬核" 的代码之一，实现了一个**自定义的二进制 Token 协议**：

```
Token 格式：版本号(3字节) + AppID(24字节) + Base64(消息体 + HMAC-SHA256签名)
消息体：Nonce + IssuedAt + ExpireAt + RoomID + UserID + 权限表
```

**关键方法**：
- `packMsg()` — 将 Token 字段打包成二进制（Little-Endian 编码）
- `serialize()` — 对消息体做 HMAC-SHA256 签名，生成最终 Token 字符串
- `addPrivilege()` — 添加权限（发布流/订阅流），`PrivPublishStream` 会自动展开为音视频+数据的三个子权限

**面试要点**：
- `ByteBuf` 是手动实现的二进制缓冲区（类似 Java 的 ByteBuffer），为什么要自己写？因为 Node.js 标准库没有现成的
- 使用了 HMAC-SHA256 做**消息认证**，防止 Token 被伪造
- Token 有过期时间（`expireAt`），24 小时后自动失效

---

### 第三层：前端 API 层 — 请求抽象

#### `src/app/api.ts` — API 定义（声明式）

```typescript
export const BasicAPIs = [
  { action: 'getScenes', apiPath: '/getScenes', method: 'post' },
] as const;

export const AigcAPIs = [
  { action: 'StartVoiceChat', apiPath: '/proxy', method: 'post' },
  { action: 'StopVoiceChat', apiPath: '/proxy', method: 'post' },
] as const;
```

每个 API 只声明三个属性：`action`（操作名）、`apiPath`（URL 路径）、`method`（HTTP 方法）。

#### `src/app/base.ts` — API 生成器（工厂模式）

```typescript
export const generateAPIs = (apiConfigs) =>
  apiConfigs.reduce((store, { action, apiPath, method }) => {
    store[action] = async (params) => {
      const queryData = method === 'get'
        ? await requestGetMethod({ action })(params)
        : await requestPostMethod({ action, apiPath })(params);
      const res = await queryData?.json();
      return resultHandler(res);  // 统一错误处理
    };
    return store;
  }, {});
```

**设计模式**：用 `reduce` 将 API 配置数组转换成方法对象：
```typescript
// 输入:
[{ action: 'StartVoiceChat', apiPath: '/proxy', method: 'post' }]
// 输出:
{ StartVoiceChat: async (params) => { /* 发送 POST 到 /proxy?Action=StartVoiceChat */ } }
```

#### `src/app/index.ts` — 统一导出

```typescript
const VoiceChat = generateAPIs(AigcAPIs);
const Basic = generateAPIs(BasicAPIs);
export default { VoiceChat, Basic };
// 使用：Apis.VoiceChat.StartVoiceChat({ SceneID: 'Custom' })
```

**面试要点**：这种**声明式 API 层**的优点：
1. 新增 API 只需加一行配置，不用重复写 fetch 逻辑
2. 统一的错误处理（`resultHandler`）
3. TypeScript 类型安全（`as const` + 泛型推导）

---

### 第四层：RTC 客户端 — 音视频核心

#### `src/lib/RtcClient.ts`

这是整个前端的**核心核心核心**，封装了所有与火山 RTC SDK 的交互。它是一个**单例类**（文件末尾 `export default new RTCClient()`）。

**核心方法分类**：

| 分类 | 方法 | 作用 |
|------|------|------|
| **生命周期** | `createEngine()` | 创建 RTC 引擎 + 注册 AI 降噪插件 |
| | `joinRoom()` | 加入 RTC 房间（传入 Token、RoomId、UserId） |
| | `leaveRoom()` | 离开房间 + 销毁引擎 |
| **设备控制** | `getDevices()` | 枚举麦克风/摄像头/扬声器设备 |
| | `startAudioCapture()` / `stopAudioCapture()` | 开关麦克风 |
| | `startVideoCapture()` / `stopVideoCapture()` | 开关摄像头 |
| | `switchDevice()` | 热切换设备（拔插耳机时） |
| **流发布** | `publishStream()` | 向房间发布音视频流 |
| | `unpublishStream()` | 取消发布 |
| **AI 控制** | `startAgent()` | **启动 AI 对话**（调后端 `/proxy?Action=StartVoiceChat`） |
| | `stopAgent()` | 关闭 AI 对话 |
| | `commandAgent()` | 向 AI 发送指令（打断/外部文本） |
| | `updateAgent()` | 更新 AI 配置（先停后启） |
| **视频渲染** | `setLocalVideoPlayer()` | 将本地视频流绑定到 DOM 元素 |
| | `setRemoteVideoPlayer()` | 将远端视频流绑定到 DOM 元素 |

**关键实现细节**：

```typescript
// AI 启动流程
startAgent = async (scene: string) => {
  if (this.audioBotEnabled) {
    await this.stopAgent(scene);  // 先停掉旧的
  }
  await Apis.VoiceChat.StartVoiceChat({ SceneID: scene });  // 调用后端
  this.audioBotEnabled = true;      // 标记 AI 已启用
  this.audioBotStartTime = Date.now();
};

// AI 打断（通过 RTC 自定义二进制消息通道）
commandAgent = ({ command, agentName, interruptMode, message }) => {
  if (this.audioBotEnabled) {
    this.engine.sendUserBinaryMessage(
      agentName,
      string2tlv(JSON.stringify({ Command: command, InterruptMode: interruptMode, Message: message }), 'ctrl')
    );
  }
};
```

**面试要点**：
- `sendUserBinaryMessage` 是 RTC 自定义消息通道，不走 HTTP，延迟极低
- 消息编码为 **TLV 格式**（Type-Length-Value），4 字节类型 + 4 字节大端长度 + 数据
- AI 降噪插件是可选的，环境不支持时静默失败（try-catch）

---

### 第五层：事件监听与语义解析

#### `src/lib/listenerHooks.ts` — RTC 事件 → Redux 的桥接

这是连接底层 RTC SDK 和上层 React UI 的**适配器层**。它将 14 种 RTC 事件映射为 Redux dispatch：

| RTC 事件 | Redux Action | UI 效果 |
|----------|-------------|---------|
| `onUserJoined` | `remoteUserJoin` | 用户列表新增 |
| `onUserLeave` | `remoteUserLeave` | 用户列表移除 |
| `onUserPublishStream` | `updateRemoteUser` | 标记用户摄像头/麦克风开 |
| `onUserUnpublishStream` | `updateRemoteUser` | 标记用户摄像头/麦克风关 |
| `onNetworkQuality` | `updateNetworkQuality` | 网络信号图标变化 |
| `onError` | （仅 log） | 重复登录等异常处理 |

**最核心的回调**：

```typescript
// AI 开始说话时，远端（AI）发布音频流
const handleUserPublishStream = (e) => {
  // 绑定 AI 的视频流到 DOM（如果有数字人）
  RtcClient.setRemoteVideoPlayer(userId, isFullScreen ? 'remote-video-player' : 'remote-full-player');
  dispatch(updateRemoteUser(payload));
};

// 收到 AI 发来的二进制消息（字幕/状态）
const handleRoomBinaryMessageReceived = (event) => {
  parser(event.message);  // 调用 handler.ts 中的语义解析器
};
```

#### `src/utils/handler.ts` — 语义解析器

收到 AI 的二进制消息后，解析出三种类型的语义数据：

| 消息类型 | TLV Type | 作用 |
|----------|----------|------|
| `BRIEF` (conv) | 3 | AI 状态变化：正在听 / 思考中 / 说话中 / 被打断 / 已完成 |
| `SUBTITLE` (subv) | 1 | 实时字幕文本（流式推送，边说边显示） |
| `FUNCTION_CALL` (tool) | 2 | AI 调用外部函数（如查天气） |

```typescript
const maps = {
  [MESSAGE_TYPE.BRIEF]: (parsed) => {
    switch (parsed.Stage.Code) {
      case AGENT_BRIEF.THINKING:  dispatch(updateAIThinkState({ isAIThinking: true }));  break;
      case AGENT_BRIEF.SPEAKING:  dispatch(updateAITalkState({ isAITalking: true }));    break;
      case AGENT_BRIEF.FINISHED:   dispatch(updateAITalkState({ isAITalking: false }));   break;
      case AGENT_BRIEF.INTERRUPTED: dispatch(setInterruptMsg());                          break;
    }
  },
  [MESSAGE_TYPE.SUBTITLE]: (parsed) => {
    // 将 AI 说的话和用户说的话存到 msgHistory
    dispatch(setHistoryMsg({ text: msg, user, paragraph, definite }));
  },
};
```

**面试要点**：
- `BRIEF` 状态机：`LISTENING → THINKING → SPEAKING → FINISHED`，被打断时 `SPEAKING → INTERRUPTED`
- `SUBTITLE` 的 `definite` vs `paragraph`：
  - 普通模式用 `definite`（是否一句完整的话）
  - 数字人模式用 `paragraph`（是否一个完整段落）
- 字幕增量更新逻辑：如果上一条没说完，就更新同一条的内容

---

### 第六层：Redux 状态管理

#### `src/store/slices/room.ts` — 最核心的 slice

这是整个应用状态的**中心枢纽**，约 300 行。管理的数据：

```typescript
interface RoomState {
  // RTC 基础信息
  roomId?: string;
  isJoined: boolean;
  localUser: LocalUser;
  remoteUsers: IUser[];

  // AI 状态
  isAIGCEnable: boolean;    // AI 是否启用
  isAIThinking: boolean;     // AI 思考中
  isAITalking: boolean;     // AI 说话中
  isUserTalking: boolean;   // 用户说话中

  // 对话数据
  msgHistory: Msg[];                     // 对话历史
  currentConversation: Record<string, { msg: string; definite: boolean }>;

  // 场景配置
  scene: string;                          // 当前选中的场景 ID
  sceneConfigMap: Record<string, SceneConfig>;  // 所有场景配置
  rtcConfigMap: Record<string, RTCConfig>;      // 所有 RTC 配置

  // UI 状态
  isShowSubtitle: boolean;
  isFullScreen: boolean;
  networkQuality: NetworkQuality;
}
```

**关键的 Reducer 逻辑**：

① `updateRTCConfig` — 更新 RTC 配置的同时，同步更新 `RtcClient.basicInfo`：
```typescript
updateRTCConfig: (state, { payload }) => {
  state.rtcConfigMap = payload;
  RtcClient.basicInfo = {
    app_id: payload[state.scene].AppId,
    room_id: payload[state.scene].RoomId,
    user_id: payload[state.scene].UserId,
    token: payload[state.scene].Token,
  };
};
```

② `setHistoryMsg` — 字幕追加逻辑（流式更新）：
```typescript
setHistoryMsg: (state, { payload }) => {
  const lastMsg = state.msgHistory.at(-1);
  const lastMsgCompleted = fromBot ? lastMsg.definite : lastMsg.paragraph;
  if (lastMsgCompleted) {
    state.msgHistory.push(payload);  // 新句子
  } else {
    lastMsg.value = payload.text;    // 更新当前句子
  }
};
```

③ `setInterruptMsg` — 打断标记（从后往前找最后一个未说完的句子）：
```typescript
setInterruptMsg: (state) => {
  for (let id = state.msgHistory.length - 1; id >= 0; id--) {
    if (!msg.definite) { state.msgHistory[id].isInterrupted = true; break; }
  }
};
```

#### `src/store/slices/device.ts` — 设备状态

管理麦克风/摄像头列表、当前选中设备、设备权限状态。

---

### 第七层：UI 页面架构

#### 页面组件树

```
MainPage
├── Header                    # 顶部栏
├── MainArea
│   ├── Antechamber           # 等待室（未进房）
│   │   ├── AIChangeCard      # 场景切换卡片
│   │   └── InvokeButton      # "呼叫 AI" 按钮
│   └── Room                  # 通话室（已进房）
│       ├── AiAvatarCard      # AI 头像/数字人
│       ├── FullScreenCard    # 全屏数字人
│       ├── CameraArea        # 摄像头/屏幕共享区域
│       ├── Conversation      # 对话字幕
│       ├── ToolBar           # 底部工具栏（麦克风/摄像头/挂断）
│       └── AudioController   # 音量控制
└── Menu                      # 右侧菜单（场景信息/版本/设备设置）
    ├── Operation             # 操作区（修改 AI 人设）
    ├── Subtitle              # 字幕开关
    ├── DeviceDrawerButton    # 设备设置抽屉
    └── 版本信息 / SDK 版本
```

#### `useJoin` — 进房 Hook（`src/lib/useCommon.ts`）

这是最核心的 Hook，串联了整个进房 + 启动 AI 的流程：

```typescript
async function disPatchJoin() {
  // 1. 检查浏览器是否支持 WebRTC
  const isSupported = await VERTC.isSupported();

  // 2. 创建 RTC 引擎
  await RtcClient.createEngine();

  // 3. 注册事件监听器（RTC 事件 → Redux）
  RtcClient.addEventListeners(listeners);

  // 4. 加入 RTC 房间
  await RtcClient.joinRoom();

  // 5. 获取设备列表
  const mediaDevices = await RtcClient.getDevices({ audio: true, video: false });

  // 6. 更新 Redux（标记已进房）
  dispatch(localJoinRoom({ roomId, user }));

  // 7. 自动打开麦克风
  await switchMic();

  // 8. 启动 AI 对话！
  await RtcClient.startAgent(id);
  dispatch(updateAIGCState({ isAIGCEnable: true }));
}
```

---

### 第八层：辅助服务 — Python 实现

> 官方原版 `Server/`（Node.js）与 `server_python/`（FastAPI 等价移植）已在改造中删除，
> 项目仅保留 `rag_llm_server/` 一个后端，入口 `main.py`（详见 docs/00 号文档）。

#### `rag_llm_server/` — 高级自定义 LLM 后端

这是一个**进阶版**后端，实现了：
1. **自定义 LLM 回调模式**：不再使用火山默认的大模型，而是通过 `CustomLLM` 模式 + SSE 流式回调，接入自己的 LLM（如豆包 Ark SDK）
2. **RAG 知识库检索**：在 AI 回答前，先从本地知识库检索相关内容，注入到 LLM 的上下文中

```python
@app.post("/api/chat_callback")
async def chat_callback(request: Request):
    messages = data.get("messages", [])
    # 1. 先从 RAG 知识库检索
    rag_content = await rag_service.retrieve(messages[-1].get("content", ""))
    # 2. 调用 LLM 流式生成
    stream_iterator = llm_service.chat_stream(messages, rag_content)
    # 3. SSE 流式返回给 RTC
    async def generate_sse():
        for chunk in stream_iterator:
            yield f"data: {chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(generate_sse(), media_type="text/event-stream")
```

---

## 5. 数据流全景：一次完整的 AI 对话

```
┌─ 用户 ─┐
│ 点击「呼叫」按钮                          │
└────────┬─────────────────────────────────┘
         │
    ┌────▼─────────────────────────────────────────────┐
    │ 1. Antechamber.handleJoinRoom()                 │
    │    调用 useJoin() → disPatchJoin()               │
    └────┬─────────────────────────────────────────────┘
         │
    ┌────▼─────────────────────────────────────────────┐
    │ 2. RtcClient.createEngine()                     │
    │    创建 WebRTC 引擎 + AI 降噪插件                  │
    └────┬─────────────────────────────────────────────┘
         │
    ┌────▼─────────────────────────────────────────────┐
    │ 3. RtcClient.joinRoom()                         │
    │    SDK 通过 UDP/TCP 连接到火山 RTC 边缘节点        │
    │    Token 鉴权通过后进入房间                        │
    └────┬─────────────────────────────────────────────┘
         │
    ┌────▼─────────────────────────────────────────────┐
    │ 4. switchMic() → startAudioCapture()            │
    │    浏览器开始采集麦克风                            │
    └────┬─────────────────────────────────────────────┘
         │
    ┌────▼─────────────────────────────────────────────┐
    │ 5. RtcClient.startAgent('Custom')               │
    │    POST /proxy?Action=StartVoiceChat             │
    │    后端调用火山 OpenAPI → 云端启动 AI Agent       │
    └────┬─────────────────────────────────────────────┘
         │
    ┌────▼──────────────────────┐
    │ 6. 用户说话                │
    │    麦克风 → RTC SDK → 火山云端 │
    └────┬──────────────────────┘
         │
    ┌────▼────────────────────────────────────────────┐
    │ 7. 火山云端处理                                  │
    │    ASR: 语音 → 文字                              │
    │    LLM: 文字 → AI 回复文字                        │
    │    TTS: AI 回复文字 → 语音                        │
    └────┬────────────────────────────────────────────┘
         │
    ┌────▼────────────────────────────────────────────┐
    │ 8. RTC 事件回调到前端                            │
    │    onRoomBinaryMessageReceived:                 │
    │      SUBTITLE → setHistoryMsg → 字幕更新         │
    │      BRIEF → updateAIThinkState/AITalkState     │
    │    onUserPublishStream: AI 音频流到达             │
    │      → 浏览器自动播放 AI 的语音                   │
    └─────────────────────────────────────────────────┘
```

---

## 6. 关键设计模式与亮点

### 6.1 RTC 单例模式

```typescript
// src/lib/RtcClient.ts
class RTCClient { /* ... */ }
export default new RTCClient();  // 整个应用只有一个实例
```

整个应用只有一个 RTC 连接，所有组件共享同一个 RTC 引擎。

### 6.2 Redux 与 RTC 的双向绑定

```
RTC 事件 ──→ listenerHooks ──→ Redux Dispatch ──→ UI 更新
                                          ↓
UI 操作 ──→ Redux Dispatch ──→ RtcClient 方法 ──→ RTC SDK
```

`updateRTCConfig` 这个 reducer 更是直接在 Redux 中修改了 `RtcClient.basicInfo`（突破纯函数的严格约定，但实用）。

### 6.3 响应式设备热切换

`handleAudioDeviceStateChanged` 监听设备插拔，自动切换到可用设备：
```typescript
if (device.deviceState === 'inactive') {
  deviceId = devices.audioInputs?.[0].deviceId || '';  // fallback 到第一个设备
}
RtcClient.switchDevice(MediaType.AUDIO, deviceId);
```

### 6.4 流式字幕的增量渲染

字幕不是一次性返回的，而是**边说边推送**：
```
"今天" → "今天天气" → "今天天气不错" → "今天天气不错，适合"
```
前端通过 `paragraph`/`definite` 标记判断是否追加新句子，实现打字机效果。

### 6.5 二进制消息通道（TLV 编码）

用 RTC 的 `sendUserBinaryMessage` + TLV 编码实现自定义语义通道，比 HTTP 轮询延迟低一个数量级。

---

## 7. 面试高频问题

### Q1: 为什么要通过自己的后端代理调火山 API，前端不能直接调吗？

**答**：因为火山 OpenAPI 需要 AK/SK（Access Key / Secret Key）签名鉴权。AK/SK 是**敏感凭证**，不能暴露在前端代码中。后端代理模式是标准的安全实践（BFF 模式）。

### Q2: RTC Token 是用来干什么的？和 AK/SK 有什么区别？

**答**：
- **AK/SK** 是服务端调用火山管理 API 用的，权限大（可以创建/管理资源），存在后端
- **RTC Token** 是前端加入 RTC 房间用的，权限小（只能进指定房间），有时效性（24小时），由后端用 AK/SK + HMAC-SHA256 签发

### Q3: WebRTC 的连接过程是怎样的？

**答**：简化版流程：
1. 客户端调用 `joinRoom(token, roomId, userId)` 
2. SDK 通过信令服务器交换 SDP（会话描述协议）信息
3. 通过 ICE（交互式连接建立）进行 NAT 穿透，找到最优传输路径
4. 建立 P2P 或中继连接（在火山场景下通常走云端 SFU 中转）

### Q4: 什么是 TLV 格式？为什么用 TLV？

**答**：TLV（Type-Length-Value）是一种二进制编码格式：
- Type（4字节）：标识数据类型（ctrl/conv/subv/func）
- Length（4字节大端）：Value 的字节长度
- Value（可变长度）：实际数据

用 TLV 的原因：比 JSON 更小、解析更快，适合 WebRTC DataChannel 的低延迟场景。

### Q5: 这个项目如何处理"打断"？

**答**：
1. 用户说话时，AI 正在播放的语音被中断
2. 云端发送 `BRIEF` 消息 `Code=INTERRUPTED`
3. 前端 `setInterruptMsg` 找到最后一个未完成的句子，标记 `isInterrupted: true`
4. 字幕上显示「已打断」标签

### Q6: 为什么 `redux-toolkit` 的 middleware 里设了 `serializableCheck: false`？

**答**：因为 Redux Store 存了 `MediaDeviceInfo`、RTC 事件对象等非纯 JSON 对象。关闭序列化检查是为了避免这些运行时对象的警告，是一个务实的取舍。

### Q7: 如果你来优化这个项目，会怎么做？

**答**：
1. **把 `RtcClient.basicInfo` 从 Redux reducer 中移出**：副作用放在 middleware 或 thunk 中，保持 reducer 纯净
2. **抽离 `msgHistory` 的更新逻辑**：当前字幕更新的 reducer 逻辑较复杂，可以抽成独立工具函数
3. **添加错误边界（Error Boundary）**：RTC 连接失败、设备权限拒绝等异常目前只靠 `Message.error` 提示
4. **移动端适配**：目前用 `isMobile()` 做条件渲染，可以改用响应式断点 + CSS 媒体查询
5. **添加单元测试**：`handler.ts` 的语义解析、`utils.ts` 的 TLV 编解码是纯函数，非常适合写测试

---

## 8. 项目启动指南

### 8.1 前置条件

| 依赖 | 版本要求 | 检查命令 |
|------|---------|----------|
| Node.js | 16.0+ | `node -v` |
| npm | 8.0+ | `npm -v` |
| Python（可选） | 3.10+ | `python -v` |

### 8.2 启动步骤

你需要**两个终端**，一个启动后端，一个启动前端。

#### 终端 1：启动 Python 后端（端口 3001）

```powershell
cd E:\dw_code\ark_aigc_demo-main\rag_llm_server
uv sync
copy .env.example .env
uv run uvicorn main:app --host 0.0.0.0 --port 3001 --workers 1
```

看到 Uvicorn 的 application startup complete 日志就成功了。`RTC_CALLBACK_SECRET` 启动必填；使用 RTC 时还必须配置公网可达的 `SERVER_URL`。

#### 终端 2：启动 React 前端（端口 3000）

```powershell
cd E:\dw_code\ark_aigc_demo-main
npm install        # 安装依赖（首次，可能需要几分钟）
npm run dev        # 启动前端
```

浏览器会自动打开 `http://localhost:3000`。

### 8.3 配置场景参数（关键！）

官方原版的 `Server/scenes/Custom.json` 已随旧后端删除。当前配置统一走 `rag_llm_server/.env`
（火山 AK/SK、RTC AppId/AppKey、方舟 ARK 密钥等，变量清单见根 README「环境变量」表）。

> 这些参数可以从火山引擎控制台获取：
> - AK/SK：https://console.volcengine.com/iam/keymanage/
> - RTC AppId/AppKey：https://console.volcengine.com/rtc/aigc/listRTC

### 8.4 启动后端

```powershell
cd E:\dw_code\ark_aigc_demo-main\rag_llm_server
uv sync               # 安装依赖（uv.lock）
uv run uvicorn main:app --host 0.0.0.0 --port 3001
# 也可 uv run python main.py；两种方式默认都不启用 reload
```

当前后端是单进程、SQLite 多用户架构。登录会话保存在 HttpOnly Cookie 中，业务数据和 RTC 标识均按用户/面试会话隔离；生产环境固定单 worker，HTTPS 下设置 `AUTH_COOKIE_SECURE=true`。旧库存在历史无归属数据时，需要在首次启动前配置 `BOOTSTRAP_ADMIN_USERNAME` 与 `BOOTSTRAP_ADMIN_PASSWORD`。CORS 仅接受 `CORS_ORIGINS`，调试接口默认不注册，设置 `ENABLE_DEBUG_ROUTES=true` 后仍要求登录。

---

## 9. 提交到码云 Git 仓库

### 9.1 检查当前 Git 状态

项目已经是干净的（没有 `.git` 目录），这是一个下载的 zip 包，不包含原始作者的 Git 历史。

### 9.2 初始化并提交

```powershell
cd E:\dw_code\ark_aigc_demo-main

# 1. 初始化 Git 仓库
git init

# 2. 添加所有文件（.gitignore 已自动排除 node_modules 等）
git add .

# 3. 首次提交
git commit -m "feat: 初始化项目 — 火山引擎 AIGC-RTC 实时对话 Demo v1.6.0"

# 4. 添加码云远程仓库（替换为你的仓库地址）
git remote add origin https://gitee.com/你的用户名/仓库名.git

# 5. 推送到码云
git push -u origin master
```

### 9.3 如果要修改作者信息

```powershell
git config user.name "你的名字"
git config user.email "你的邮箱"
```

然后重新 `git commit --amend --reset-author` 再推送。

---

## 总结学习路线图

按这个顺序学习效果最好：

```
1. 先跑起来 → 看效果
2. 读懂 README.md → 了解业务概念
3. App.tsx + MainPage → 理解页面结构
4. app/api.ts + app/base.ts → 理解 API 层设计
5. rag_llm_server/main.py + api/rtc.py + services/rtc_service.py → 理解应用装配与 RTC 代理边界
6. RtcClient.ts → 理解 RTC 封装（核心）
7. listenerHooks.ts + handler.ts → 理解事件流
8. store/slices/room.ts → 理解状态管理
9. Room/Conversation.tsx → 理解字幕实时更新
10. token.js → 理解密码学实现（高级）
11. rag_llm_server/ → 理解自定义 LLM + RAG（高级）
```

祝学习顺利，面试成功！🎉
