# XHS_ALL_IN_ONE — AI 编码规则

## 一、项目总览

XHS_ALL_IN_ONE 是一站式小红书运营平台。

- **前端**：React 19 + TypeScript 5.9 + Ant Design 6 + Vite 7
- **后端**：FastAPI + Python 3.11+ + SQLAlchemy 2.0 + Pydantic + APScheduler
- **SDK 层**：`apis/` + `xhs_utils/` — 小红书逆向签名算法封装，**禁止直接修改**
- **签名引擎**：`static/` — JS 签名文件，**禁止直接修改**

---

## 二、架构分层与边界

### 前端分层（`frontend/src/`）

```
types/         → 纯类型定义层，保证全链路类型安全
lib/           → API 客户端（axios）、工具函数
  api.ts       → 唯一 API 调用入口，所有接口通过此文件
hooks/         → 自定义 React Hooks
components/    → 可复用 UI 组件
pages/         → 视图层，错误边界（Error Boundary）
```

**规则**：
- `pages/` 不得直接调用 `axios`，必须通过 `lib/api.ts`
- `lib/api.ts` 不得包含 UI 渲染逻辑
- 禁止在 `components/` 中直接调用 API（通过 props/callback 传入）

### 后端分层（`backend/app/`）

```
api/       → HTTP 路由层（FastAPI routers），负责参数校验、响应格式化
services/  → 业务逻辑层，封装领域逻辑
adapters/  → 适配器层，隔离 SDK（apis/ + xhs_utils/）与业务代码
core/      → 基础设施层（配置、数据库、安全、时区）
models/    → SQLAlchemy ORM 模型层
schemas/   → Pydantic 响应辅助
```

**规则**：
- `api/` 可以 import `services/`、`models/`、`core/`
- `services/` 可以 import `adapters/`、`models/`、`core/`，不得 import `api/`
- `adapters/` 只能 import `core/` 和外部 SDK（`apis/`、`xhs_utils/`）
- 禁止循环 import

---

## 三、Ant Design 熔断机制

遇到以下场景时，**必须 100% 优先使用 Ant Design**，禁止手写基础组件：

| 场景 | 必须使用 |
|------|---------|
| 表格 | `<Table />` |
| 列表 | `<List />` / `<Card />` |
| 表单 + 输入框 | `<Form />` + `<Input />` |
| 弹窗 / 抽屉 | `<Modal />` / `<Drawer />` |
| 按钮 | `<Button />` |
| 标签 / 徽标 | `<Tag />` / `<Badge />` |
| 消息提示 | `<message />` / `<notification />` |
| 选择器 / 日期 | `<Select />` / `<DatePicker />` |
| 图标 | `@ant-design/icons` / `lucide-react` |
| 布局 | `<Row />` + `<Col />` / `<Flex />` / `<Space />` |
| 加载状态 | `<Spin />` / `<Skeleton />` |
| 空状态 | `<Empty />` |
| 步骤 | `<Steps />` |
| 分页 | `<Pagination />` |
| 开关 | `<Switch />` |
| 上传 | `<Upload />` |

**熔断条件**：如果 AI 准备手写 `display: flex; justify-content: space-between` 等基础布局或手写列表/卡片 DOM + CSS，必须触发熔断，强制思考 Ant Design 是否有现成组件。仅当 Ant Design 无法实现且**明确向用户说明并获批准后**，才允许手写。

---

## 四、错误处理规范

### 前端

| 层 | 规范 |
|----|------|
| `lib/api.ts` | axios 拦截器统一处理 401（refresh token）、网络错误。不吞错 |
| `pages/` 组件 | 禁止空 `catch`。必须：`setError()` 显示错误 UI，或 `message.error()` 提示用户 |
| `hooks/` | 调用 API 时必须处理 loading/error 状态，不得直接透传未处理的 Promise |

```tsx
// ✅ 正确
try {
  const data = await fetchAccounts();
  setAccounts(data);
} catch {
  setError("加载失败");
} finally {
  setIsLoading(false);
}

// ❌ 禁止：空 catch
try { /* ... */ } catch { /* 什么都没做 */ }

// ❌ 禁止：catch 后只 console.log
try { /* ... */ } catch (e) { console.error(e); }
```

### 后端

| 层 | 规范 |
|----|------|
| `api/` | **唯一允许 try/catch 的层**。捕获异常并转为 HTTP 响应，统一报错格式 |
| `services/` | **禁止 try/catch 掩盖错误**。业务失败直接抛异常（`HTTPException` 或自定义异常），或返回明确的结果类型。**禁止返回 `[]` / `{}` 等伪成功值掩盖错误** |
| `adapters/` | 捕获 SDK 异常并包装为明确的异常类型，不得吞错 |

```python
# ✅ api/ 层：正确
@router.get("/notes")
def get_notes(...):
    try:
        result = service.do_something()
    except ServiceError as e:
        raise HTTPException(status_code=400, detail=str(e))

# ❌ 禁止：services/ 层用 try/catch 吞错
def service_func():
    try:
        # 业务逻辑
        ...
    except Exception:
        return []  # ❌ 禁止掩盖错误
```

---

## 五、类型安全

### 前端
- **禁止滥用 `any`**。无法确定类型时使用 `unknown` + 类型守卫
- **禁止 `@ts-ignore`**、`@ts-expect-error`
- API 响应数据应用类型定义（已有 `types/index.ts`），确保消费端类型安全
- 前端 API 响应参数使用 Pydantic `BaseModel`（后端）确保前后端类型一致

```tsx
// ✅ 正确
function handle(data: unknown) {
  if (typeof data === "string") { /* ... */ }
}

// ❌ 禁止
function handle(data: any) { /* ... */ }
```

### 后端
- 所有函数必须标注返回类型注解
- 路由函数使用 Pydantic `BaseModel` 定义请求/响应模型（已有模式）
- 禁止使用 `dict` 作为万能返回值类型，应定义明确的 TypedDict 或 Pydantic 模型

---

## 六、代码风格与 Lint

### 修改代码后必须运行

```bash
# 前端检查
cd frontend && npx eslint src/ --max-warnings 0

# 后端检查
ruff check backend/ tests/ spider/ xhs_utils/

# 一并执行
cd frontend && npx eslint src/ --max-warnings 0 && cd .. && ruff check backend/ tests/ spider/ xhs_utils/
```

### 通用规则
- 禁止带病交付：lint 未通过时不得声称任务完成
- 遵循现有代码风格（缩进、命名、文件组织与已有代码一致）
- 避免魔数（Magic Number），使用常量或配置
- 避免过度设计：不提前做未来可能需要但当前不必要的抽象

### 命名规范
- **前端**：文件名小写中横线（`accounts-page.tsx`），组件名 PascalCase（`XhsAccountsPage`）
- **后端**：文件名蛇形（`account_service.py`），函数名蛇形（`serialize_account`），类名 PascalCase（`PlatformAccount`）
- 保持与项目中已有代码一致的命名风格

---

## 七、SDK 与签名文件保护

- `apis/` 和 `xhs_utils/` 是底层 SDK 层，**禁止直接修改**
- `static/*.js` 是签名引擎文件，**禁止直接修改**
- 上层通过 `backend/app/adapters/` 中转调用 SDK，**不得绕过 adapter 直接 import SDK**
- 当前模板中 `adapters/xhs/` 下的每个 adapter 方法必须使用 `with direct_xhs_request_env():` 包装 SDK 调用

---

## 八、平台防坑指南

### XHS SDK 常见问题
- **签名过期**：JS 签名文件可能随平台更新而失效，调用失败时优先怀疑签名问题
- **频率限制**：`rate_limiter.py` 提供滑动窗口限流，爬取任务必须经过限流器
- **Cookie 失效**：2h 自动健康巡检，适配层调用前确保 Cookie 有效
- **代理干扰**：`direct_xhs_request_env()` 用于临时移除代理环境变量，防止本地代理破坏 SDK 请求

### 数据库
- 所有时间使用 `core/time.py` 的 `shanghai_now()`（UTC+8）
- 敏感数据（Cookie、API Key）使用 `core/security.py` 的 Fernet 加密存储
- 所有模型有 `user_id` 外键，查询必须带用户过滤

---

## 九、注释规范

- **不要添加注释来解释 "是什么"** — 代码本身应自解释
- **不要为明显的逻辑添加注释**（如 `// 增加计数`）
- 仅在以下情况添加注释：
  - 解释 "为什么" 采用某种非常规实现
  - 说明与平台相关的 workaround / hack
  - 记录业务规则中的边界条件或隐含约束
- 函数/组件命名应表意清晰，注释应为最后手段

---

## 十、安装与运行

```bash
# 安装所有依赖
pip install -r requirements.txt
npm install
cd frontend && npm install && cd ..

# 启动
python main.py --with-frontend
# 前端: http://localhost:5173
# API:  http://localhost:8000/docs
```
