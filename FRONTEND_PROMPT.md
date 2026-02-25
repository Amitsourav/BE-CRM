# Admitverse CRM — Frontend Build Prompt

> **Stack:** Next.js 14 (App Router) · TypeScript · Tailwind CSS · shadcn/ui · Supabase Auth (client-side) · Axios

---

## 0 — Environment & Bootstrap

```bash
npx create-next-app@latest admitverse-crm --typescript --tailwind --eslint --app --src-dir --import-alias "@/*"
cd admitverse-crm
npx shadcn@latest init          # select "New York" theme, slate base colour, CSS variables = yes
npx shadcn@latest add button card input label select textarea badge table dialog sheet tabs avatar dropdown-menu popover calendar command separator scroll-area skeleton toast sonner checkbox radio-group switch progress tooltip alert-dialog
npm i axios zustand @supabase/supabase-js @supabase/auth-helpers-nextjs date-fns react-beautiful-dnd lucide-react recharts
```

Copy the `.env.ui` file from BE-CRM to `.env.local` inside the Next.js project, adjusting the real Supabase values.

```env
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
NEXT_PUBLIC_APP_NAME=Admitverse CRM
```

---

## 1 — Project Structure

```
src/
├── app/
│   ├── (auth)/
│   │   ├── login/page.tsx
│   │   ├── reset-password/page.tsx
│   │   └── update-password/page.tsx
│   ├── (dashboard)/
│   │   ├── layout.tsx                 # Sidebar + Topbar shell
│   │   ├── page.tsx                   # Home → redirect by role
│   │   ├── leads/
│   │   │   ├── page.tsx              # Lead list/table
│   │   │   ├── [id]/page.tsx         # Lead detail
│   │   │   └── import/page.tsx       # CSV import wizard
│   │   ├── pipeline/page.tsx          # Kanban board
│   │   ├── tasks/page.tsx             # Task list
│   │   ├── notifications/page.tsx     # Notification centre
│   │   ├── settings/
│   │   │   └── profile/page.tsx       # My profile
│   │   └── admin/
│   │       ├── users/page.tsx         # User management
│   │       ├── users/[id]/page.tsx    # User detail + stats
│   │       ├── sources/page.tsx       # Lead sources
│   │       ├── reports/page.tsx       # Reports dashboard
│   │       └── csv-history/page.tsx   # CSV import history
│   ├── layout.tsx                     # Root layout, fonts, Toaster
│   └── globals.css
├── components/
│   ├── layout/
│   │   ├── sidebar.tsx
│   │   ├── topbar.tsx
│   │   └── mobile-nav.tsx
│   ├── leads/
│   │   ├── lead-table.tsx
│   │   ├── lead-filters.tsx
│   │   ├── lead-form.tsx
│   │   ├── lead-detail-tabs.tsx
│   │   ├── lead-timeline.tsx
│   │   ├── lead-assign-dialog.tsx
│   │   ├── bulk-assign-dialog.tsx
│   │   └── lead-stage-badge.tsx
│   ├── pipeline/
│   │   ├── pipeline-board.tsx
│   │   ├── pipeline-column.tsx
│   │   └── pipeline-card.tsx
│   ├── calls/
│   │   ├── call-log-form.tsx
│   │   └── call-history.tsx
│   ├── tasks/
│   │   ├── task-table.tsx
│   │   ├── task-form.tsx
│   │   ├── task-filters.tsx
│   │   └── task-complete-dialog.tsx
│   ├── csv/
│   │   ├── csv-upload.tsx
│   │   ├── csv-column-mapper.tsx
│   │   ├── csv-preview-table.tsx
│   │   └── csv-import-progress.tsx
│   ├── notifications/
│   │   ├── notification-bell.tsx
│   │   └── notification-list.tsx
│   ├── reports/
│   │   ├── dashboard-cards.tsx
│   │   ├── pipeline-chart.tsx
│   │   ├── agent-performance-table.tsx
│   │   ├── source-performance-chart.tsx
│   │   ├── trends-chart.tsx
│   │   └── task-compliance-chart.tsx
│   ├── users/
│   │   ├── user-table.tsx
│   │   ├── user-form.tsx
│   │   └── user-stats-card.tsx
│   └── shared/
│       ├── data-table.tsx            # Generic reusable table with sorting/pagination
│       ├── pagination.tsx
│       ├── page-header.tsx
│       ├── empty-state.tsx
│       ├── confirm-dialog.tsx
│       ├── loading-skeleton.tsx
│       └── search-input.tsx
├── lib/
│   ├── api.ts                         # Axios instance + interceptors
│   ├── supabase-client.ts             # Browser Supabase client
│   ├── utils.ts                       # cn() helper, formatters
│   └── constants.ts                   # Stages, dispositions, task types
├── hooks/
│   ├── use-auth.ts
│   ├── use-leads.ts
│   ├── use-tasks.ts
│   ├── use-notifications.ts
│   ├── use-reports.ts
│   └── use-debounce.ts
├── stores/
│   ├── auth-store.ts                  # Zustand: user, token, role
│   └── notification-store.ts          # Zustand: unread count cache
├── types/
│   └── index.ts                       # All TypeScript interfaces
└── middleware.ts                       # Route protection
```

---

## 2 — TypeScript Types (`src/types/index.ts`)

```ts
/* ── Enums ── */
export type Role = "admin" | "agent";

export type LeadStage =
  | "lead"
  | "called"
  | "connected"
  | "qualified_lead"
  | "won"
  | "lost";

export type CallDisposition =
  | "dnp"
  | "connected"
  | "busy"
  | "switched_off"
  | "wrong_number"
  | "callback";

export type TaskType =
  | "follow_up"
  | "call"
  | "meeting"
  | "document_collection"
  | "application"
  | "other";

export type TaskStatus = "pending" | "in_progress" | "completed" | "overdue";

export type NotificationType =
  | "lead_assigned"
  | "task_created"
  | "task_overdue"
  | "dnp_warning"
  | "dnp_auto_lost"
  | "stage_changed"
  | "csv_import_complete"
  | "general";

export type SourceType = "csv" | "meta_ads" | "manual" | "whatsapp";

export type CSVImportStatus =
  | "uploaded"
  | "previewing"
  | "processing"
  | "completed"
  | "failed";

/* ── Models ── */
export interface User {
  id: string;
  email: string;
  full_name: string;
  phone?: string;
  role: Role;
  is_active: boolean;
  vertical?: string;
  avatar_url?: string;
  created_at: string;
  updated_at: string;
}

export interface Lead {
  id: string;
  full_name: string;
  email?: string;
  phone?: string;
  alternate_phone?: string;
  date_of_birth?: string;
  gender?: string;
  city?: string;
  state?: string;
  country: string;
  pincode?: string;
  highest_qualification?: string;
  stream?: string;
  passing_year?: number;
  college_name?: string;
  university?: string;
  percentage?: number;
  target_degree?: string;
  target_intake?: string;
  preferred_countries?: string[];
  preferred_universities?: string[];
  current_stage: LeadStage;
  assigned_agent_id?: string;
  assigned_agent?: User;
  lead_source_id?: string;
  lead_source?: LeadSource;
  call_attempt_count: number;
  due_date?: string;
  connected_time?: string;
  won_time?: string;
  lost_time?: string;
  lost_reason?: string;
  custom_fields?: Record<string, unknown>;
  tags?: string[];
  notes?: string;
  last_call_provider?: string;
  last_call_recording_url?: string;
  created_by?: string;
  created_at: string;
  updated_at: string;
}

export interface Task {
  id: string;
  lead_id?: string;
  lead?: Lead;
  assigned_to: string;
  assignee?: User;
  created_by: string;
  creator?: User;
  task_type: TaskType;
  title: string;
  description?: string;
  status: TaskStatus;
  due_date: string;
  completed_at?: string;
  completion_notes?: string;
  stage_log_id?: string;
  created_at: string;
  updated_at: string;
}

export interface CallAttempt {
  id: string;
  lead_id: string;
  agent_id: string;
  agent?: User;
  attempt_number: number;
  disposition: CallDisposition;
  conversation_notes: string;
  agent_agenda: string;
  due_date_for_next?: string;
  call_provider?: string;
  call_recording_url?: string;
  external_call_id?: string;
  call_duration_seconds?: number;
  created_at: string;
}

export interface LeadStageLog {
  id: string;
  lead_id: string;
  from_stage?: LeadStage;
  to_stage: LeadStage;
  changed_by: string;
  changed_by_user?: User;
  conversation_notes?: string;
  agent_agenda?: string;
  due_date_set?: string;
  created_at: string;
}

export interface LeadSource {
  id: string;
  name: string;
  source_type: SourceType;
  meta_form_id?: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Notification {
  id: string;
  user_id: string;
  type: NotificationType;
  title: string;
  message: string;
  is_read: boolean;
  lead_id?: string;
  task_id?: string;
  created_at: string;
}

export interface CSVImport {
  id: string;
  uploaded_by: string;
  file_name: string;
  status: CSVImportStatus;
  total_rows: number;
  success_count: number;
  failure_count: number;
  duplicate_count: number;
  error_details: Array<{ row: number; error: string }>;
  column_mapping: Record<string, string>;
  raw_headers: string[];
  lead_source_id?: string;
  assigned_agent_id?: string;
  created_at: string;
  updated_at: string;
}

/* ── API Shapes ── */
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user_id: string;
}

export interface CSVPreview {
  import_id: string;
  file_name: string;
  raw_headers: string[];
  suggested_mapping: Record<string, string>;
  preview_rows: Record<string, string>[];
}

export interface DashboardReport {
  total_leads: number;
  leads_by_stage: Record<LeadStage, number>;
  total_calls_today: number;
  total_tasks_pending: number;
  total_tasks_overdue: number;
  conversion_rate: number;
}

export interface PipelineReport {
  stages: Array<{
    stage: LeadStage;
    count: number;
    percentage: number;
  }>;
}

export interface AgentReport {
  agent: User;
  total_leads: number;
  leads_by_stage: Record<LeadStage, number>;
  total_calls: number;
  tasks_completed: number;
  tasks_overdue: number;
}

export interface SourceReport {
  source: LeadSource;
  total_leads: number;
  conversion_rate: number;
  leads_by_stage: Record<LeadStage, number>;
}

export interface TrendDataPoint {
  date: string;
  leads: number;
  calls: number;
  conversions: number;
}

export interface UserStats {
  total_leads: number;
  leads_by_stage: Record<LeadStage, number>;
  total_calls: number;
  tasks_completed: number;
  tasks_pending: number;
  tasks_overdue: number;
}
```

---

## 3 — API Client (`src/lib/api.ts`)

```ts
import axios from "axios";

const api = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL,
  headers: { "Content-Type": "application/json" },
});

// Attach token
api.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("access_token");
    if (token) config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Handle 401 → try refresh, else redirect to /login
api.interceptors.response.use(
  (res) => res,
  async (error) => {
    const original = error.config;
    if (error.response?.status === 401 && !original._retry) {
      original._retry = true;
      try {
        const refresh = localStorage.getItem("refresh_token");
        const { data } = await axios.post(
          `${process.env.NEXT_PUBLIC_API_URL}/auth/refresh`,
          { refresh_token: refresh }
        );
        localStorage.setItem("access_token", data.access_token);
        localStorage.setItem("refresh_token", data.refresh_token);
        original.headers.Authorization = `Bearer ${data.access_token}`;
        return api(original);
      } catch {
        localStorage.clear();
        window.location.href = "/login";
      }
    }
    return Promise.reject(error);
  }
);

export default api;
```

---

## 4 — Supabase Client (`src/lib/supabase-client.ts`)

```ts
import { createBrowserClient } from "@supabase/ssr";

export const supabase = createBrowserClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
);
```

> **Note:** We use the Supabase client only for potential future real-time subscriptions. All API calls go through the Axios instance to the FastAPI backend.

---

## 5 — Auth Store (`src/stores/auth-store.ts`)

```ts
import { create } from "zustand";
import api from "@/lib/api";
import type { User, AuthTokens } from "@/types";

interface AuthState {
  user: User | null;
  isLoading: boolean;
  isAdmin: boolean;

  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  fetchMe: () => Promise<void>;
  reset: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isLoading: true,
  isAdmin: false,

  login: async (email, password) => {
    const { data } = await api.post<AuthTokens>("/auth/login", {
      email,
      password,
    });
    localStorage.setItem("access_token", data.access_token);
    localStorage.setItem("refresh_token", data.refresh_token);
    // Fetch profile
    const me = await api.get<User>("/users/me");
    set({ user: me.data, isAdmin: me.data.role === "admin", isLoading: false });
  },

  logout: async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      localStorage.clear();
      set({ user: null, isAdmin: false });
    }
  },

  fetchMe: async () => {
    try {
      const { data } = await api.get<User>("/users/me");
      set({ user: data, isAdmin: data.role === "admin", isLoading: false });
    } catch {
      set({ user: null, isAdmin: false, isLoading: false });
    }
  },

  reset: () => set({ user: null, isAdmin: false, isLoading: false }),
}));
```

---

## 6 — Middleware (`src/middleware.ts`)

```ts
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const publicPaths = ["/login", "/reset-password", "/update-password"];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const token = request.cookies.get("access_token")?.value;

  // Allow public paths
  if (publicPaths.some((p) => pathname.startsWith(p))) {
    return NextResponse.next();
  }

  // No token → redirect to login
  if (!token) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api).*)"],
};
```

> **Implementation note:** After login, also set `access_token` as an httpOnly cookie (via a Next.js Route Handler at `/api/auth/set-cookie`) so the middleware can read it. The Axios interceptor keeps using `localStorage` for API calls.

---

## 7 — Constants (`src/lib/constants.ts`)

```ts
import type { LeadStage, CallDisposition, TaskType, TaskStatus } from "@/types";

export const STAGE_CONFIG: Record<
  LeadStage,
  { label: string; color: string; order: number }
> = {
  lead: { label: "New Lead", color: "bg-slate-500", order: 0 },
  called: { label: "Called", color: "bg-blue-500", order: 1 },
  connected: { label: "Connected", color: "bg-yellow-500", order: 2 },
  qualified_lead: { label: "Qualified", color: "bg-purple-500", order: 3 },
  won: { label: "Won", color: "bg-green-500", order: 4 },
  lost: { label: "Lost", color: "bg-red-500", order: 5 },
};

export const VALID_TRANSITIONS: Record<LeadStage, LeadStage[]> = {
  lead: ["called", "lost"],
  called: ["connected", "lost"],
  connected: ["qualified_lead", "lost"],
  qualified_lead: ["won", "lost"],
  won: [],
  lost: ["lead"], // admin-only reopen
};

export const DISPOSITION_LABELS: Record<CallDisposition, string> = {
  dnp: "Did Not Pick",
  connected: "Connected",
  busy: "Busy",
  switched_off: "Switched Off",
  wrong_number: "Wrong Number",
  callback: "Callback Requested",
};

export const TASK_TYPE_LABELS: Record<TaskType, string> = {
  follow_up: "Follow Up",
  call: "Call",
  meeting: "Meeting",
  document_collection: "Document Collection",
  application: "Application",
  other: "Other",
};

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  pending: "Pending",
  in_progress: "In Progress",
  completed: "Completed",
  overdue: "Overdue",
};
```

---

## 8 — Layout Shell

### `src/app/(dashboard)/layout.tsx`

A sidebar layout with:

- **Sidebar** (collapsible on mobile):
  - Logo / App Name
  - Nav items: Dashboard, Leads, Pipeline, Tasks, Notifications
  - Admin section (only if `role === "admin"`): Users, Sources, Reports, CSV History
  - Bottom: User avatar + name + logout
- **Topbar**:
  - Breadcrumb (dynamic from pathname)
  - Search input → calls `GET /leads/search?q=`
  - Notification bell with unread count badge (poll `GET /notifications/unread-count` every 30s)
  - User dropdown (Profile, Logout)

Use shadcn `Sheet` for mobile sidebar. Use `ScrollArea` for sidebar content.

---

## 9 — Pages & Features (Detailed)

### 9.1 — Login Page (`/login`)

- Email + password form using shadcn `Input`, `Button`, `Label`
- Calls `POST /auth/login`
- Stores tokens in `localStorage` + sets cookie for middleware
- "Forgot password?" link → `/reset-password`
- On success → redirect to `/leads` (agent) or `/admin/reports` (admin)
- Show toast on error via `sonner`

### 9.2 — Reset Password (`/reset-password`)

- Email input → calls `POST /auth/reset-password`
- Shows success message regardless (security)

### 9.3 — Update Password (`/update-password`)

- New password + confirm → calls `PUT /auth/update-password`
- Requires valid token in URL (from email link)

---

### 9.4 — Leads List (`/leads`)

**Components:** `lead-table.tsx`, `lead-filters.tsx`, `search-input.tsx`, `data-table.tsx`, `pagination.tsx`

- **Filters bar** (top):
  - Stage dropdown (all stages)
  - Agent dropdown (admin only, lists users with `role=agent`)
  - Source dropdown (from `GET /leads/sources/list`)
  - Date range picker (date_from, date_to)
  - Search input (debounced, calls `GET /leads/search?q=`)
  - "Clear filters" button
  - "Import CSV" button → navigates to `/leads/import`
  - "Add Lead" button → opens `lead-form` in a `Dialog`

- **Table columns:**
  - Checkbox (for bulk select)
  - Name (link to `/leads/[id]`)
  - Phone
  - Email
  - Stage (coloured `Badge` using STAGE_CONFIG)
  - Source
  - Assigned Agent
  - Due Date (red if overdue)
  - Created At
  - Actions dropdown (View, Edit, Assign, Delete if admin)

- **Bulk actions bar** (appears when rows selected, admin only):
  - "Assign Selected" → opens `bulk-assign-dialog`
  - "Delete Selected" (with confirmation)

- **Pagination:** Server-side using `page` and `page_size` query params
- **API:** `GET /leads?page=&page_size=&stage=&agent_id=&source_id=&date_from=&date_to=`

### 9.5 — Lead Detail (`/leads/[id]`)

**Components:** `lead-detail-tabs.tsx`, `lead-timeline.tsx`, `call-log-form.tsx`, `call-history.tsx`, `task-table.tsx`, `task-form.tsx`, `lead-stage-badge.tsx`

- **Header:**
  - Full name, stage badge, assigned agent
  - Action buttons: "Change Stage" (dropdown with valid transitions), "Log Call", "Create Task", "Edit Lead"
  - Admin: "Assign" button, "Delete" button

- **Tabs** (using shadcn `Tabs`):

  **Tab 1 — Profile:**
  - Two-column card layout
  - Personal: name, email, phone, alternate phone, DOB, gender
  - Location: city, state, country, pincode
  - Education: qualification, stream, passing year, college, university, percentage
  - Preferences: target degree, intake, preferred countries, preferred universities
  - Meta: tags (editable), notes (editable), custom fields
  - Source info, created by, dates

  **Tab 2 — Timeline:**
  - Calls `GET /leads/{id}/timeline` (stage history)
  - Vertical timeline component showing:
    - Stage transitions with from→to, changed by, notes, date
    - Colour-coded by stage

  **Tab 3 — Calls:**
  - Calls `GET /leads/{id}/calls`
  - Table: attempt #, disposition badge, notes, agenda, date, duration, recording link
  - "Log Call" button → opens `call-log-form` dialog

  **Tab 4 — Tasks:**
  - Calls `GET /leads/{id}/tasks`
  - Table: title, type, status, due date, assignee
  - "Create Task" button → opens `task-form` dialog

- **Stage Change Dialog:**
  - Select target stage (only valid transitions from `VALID_TRANSITIONS`)
  - If transitioning to `called`, `connected`, or `qualified_lead`: require `conversation_notes` and `agent_agenda`
  - If transitioning to `lost`: require `lost_reason`
  - Optional `due_date` picker
  - Calls `POST /leads/{id}/stage`

- **Call Log Dialog** (`call-log-form.tsx`):
  - Disposition select (from DISPOSITION_LABELS)
  - Conversation notes textarea (required)
  - Agent agenda textarea (required)
  - Due date for next call (optional date picker)
  - Calls `POST /leads/{id}/calls`

### 9.6 — Lead Form (Create/Edit Dialog)

- **Fields:** full_name*, email, phone, alternate_phone, date_of_birth, gender, city, state, country, pincode, highest_qualification, stream, passing_year, college_name, university, percentage, target_degree, target_intake, preferred_countries (multi-input), preferred_universities (multi-input), notes, tags (multi-input)
- **On create:** `POST /leads` → on success navigate to detail page
- **On edit:** `PUT /leads/{id}` → refetch data

### 9.7 — Pipeline Board (`/pipeline`)

**Components:** `pipeline-board.tsx`, `pipeline-column.tsx`, `pipeline-card.tsx`

- **Kanban-style board** using `react-beautiful-dnd`
- **6 columns** (one per stage in order): Lead → Called → Connected → Qualified → Won → Lost
- Each column header shows: stage name, count badge
- **Cards** show: name, phone, source, due date, assigned agent avatar
- **Drag & drop** between columns:
  - Validate against `VALID_TRANSITIONS` — reject invalid drops with toast
  - On valid drop → open stage change dialog (requires notes for certain stages)
  - On success → optimistically move card, call `POST /leads/{id}/stage`
- **Filter bar:** agent filter, source filter, search
- **Data:** Fetch all leads (or paginated per column) from `GET /leads?stage=X`
- Clicking a card → navigates to `/leads/[id]`

### 9.8 — Tasks (`/tasks`)

**Components:** `task-table.tsx`, `task-filters.tsx`, `task-form.tsx`, `task-complete-dialog.tsx`

- **Filter tabs** (top): All | Today | Overdue | Completed Today
  - "All" → `GET /tasks`
  - "Today" → `GET /tasks/today`
  - "Overdue" → `GET /tasks/overdue`
  - "Completed Today" → `GET /tasks/completed-today`
- **Filters:** status dropdown, assigned_to dropdown (admin sees all agents, agent sees only self)
- **"Create Task" button** → opens task form dialog
- **Table columns:** Title, Type badge, Lead (link), Status badge, Assignee, Due Date (red if overdue), Created, Actions
- **Actions:** View lead, Edit task, Mark complete
- **Mark Complete dialog** (`task-complete-dialog.tsx`):
  - Optional `completion_notes` textarea
  - Calls `POST /tasks/{id}/complete`
- **Edit task:** opens task form with pre-filled values → `PUT /tasks/{id}`
- **Create task form** (`task-form.tsx`):
  - Title*, description, task_type select, due_date* picker, lead search/select (optional, with command palette to search leads)
  - Calls `POST /tasks`
- **Pagination:** server-side

### 9.9 — Notifications (`/notifications`)

**Components:** `notification-list.tsx`, `notification-bell.tsx`

- **Notification bell** (in topbar):
  - Shows unread count badge
  - Polls `GET /notifications/unread-count` every 30 seconds
  - Click → opens notification popover or navigates to `/notifications`

- **Notification page:**
  - Lists all notifications with pagination (`GET /notifications`)
  - Each item shows: icon (by type), title, message, timestamp, read/unread indicator
  - Click "Mark as read" per item → `PUT /notifications/{id}/read`
  - "Mark all as read" button → `PUT /notifications/read-all`
  - Clickable notifications: if `lead_id` → navigate to `/leads/[lead_id]`, if `task_id` → navigate to `/tasks` with filter

### 9.10 — CSV Import Wizard (`/leads/import`)

**Components:** `csv-upload.tsx`, `csv-column-mapper.tsx`, `csv-preview-table.tsx`, `csv-import-progress.tsx`

**Step 1 — Upload:**
- Drag-and-drop zone or file picker (accept `.csv`)
- "Download Template" link → calls `GET /csv/template` (downloads file)
- On file select → `POST /csv/upload` (multipart form)
- Show file name, size validation feedback
- On success → move to step 2

**Step 2 — Map Columns:**
- Calls `POST /csv/{import_id}/preview`
- Shows raw CSV headers on left, target field dropdowns on right
- Pre-fills suggested mapping from API response
- Preview table shows first 5 rows with mapped column names
- Select agent to assign (optional, dropdown of agents)
- Select lead source (optional, dropdown of sources)
- "Process Import" button → move to step 3

**Step 3 — Processing:**
- Calls `POST /csv/{import_id}/process` with column_mapping, assigned_agent_id, lead_source_id
- Show progress indicator
- Poll `GET /csv/{import_id}/status` every 2 seconds until status is `completed` or `failed`

**Step 4 — Results:**
- Show summary card: total rows, success count (green), failures (red), duplicates (yellow)
- If errors → expandable section showing error_details (row number + error message)
- "Go to Leads" button, "Import Another" button

### 9.11 — Profile Settings (`/settings/profile`)

- Shows current user data from `GET /users/me`
- Editable fields: full_name, phone, vertical, avatar_url
- Calls `PUT /users/me`
- Show toast on success

---

### 9.12 — Admin: User Management (`/admin/users`)

> All admin pages are wrapped in a guard that checks `role === "admin"`.

- **User table:** full_name, email, role badge, vertical, is_active toggle, created_at, actions
- **"Register User" button** → opens dialog:
  - Fields: email*, password*, full_name*, phone, role* (admin/agent), vertical
  - Calls `POST /auth/register`
- **Actions:** View stats, Edit, Deactivate (with confirmation)
- **Filters:** role dropdown, is_active toggle
- **API:** `GET /users?role=&is_active=`

### 9.13 — Admin: User Detail (`/admin/users/[id]`)

- User profile card (from `GET /users/{id}`)
- Edit form → `PUT /users/{id}`
- **Stats section** (from `GET /users/{id}/stats`):
  - Total leads, leads by stage (mini bar chart)
  - Total calls made
  - Tasks: completed, pending, overdue
- Deactivate button → `DELETE /users/{id}` (with confirmation dialog)

### 9.14 — Admin: Lead Sources (`/admin/sources`)

- Table of sources from `GET /leads/sources/list`
- Columns: name, source_type badge, meta_form_id (if meta_ads), is_active, created_at
- "Add Source" button → dialog with name, source_type, meta_form_id fields → `POST /leads/sources`

### 9.15 — Admin: Reports Dashboard (`/admin/reports`)

**Components:** `dashboard-cards.tsx`, `pipeline-chart.tsx`, `agent-performance-table.tsx`, `source-performance-chart.tsx`, `trends-chart.tsx`, `task-compliance-chart.tsx`

- **Top row — KPI Cards** (from `GET /reports/dashboard`):
  - Total Leads (with stage breakdown sparkline)
  - Calls Today
  - Pending Tasks
  - Overdue Tasks
  - Conversion Rate (as percentage)

- **Pipeline Funnel** (from `GET /reports/pipeline`):
  - Horizontal bar chart or funnel visualisation showing leads per stage
  - Use `recharts` BarChart

- **Agent Performance Table** (from `GET /reports/agents`):
  - Columns: Agent Name, Total Leads, Won, Lost, Calls Made, Tasks Completed, Tasks Overdue
  - Click row → `GET /reports/agents/{id}` for detailed view

- **Source Performance** (from `GET /reports/sources`):
  - Pie chart or bar chart showing leads by source
  - Show conversion rate per source

- **Trends Chart** (from `GET /reports/trends?days=30`):
  - Line chart with date on X axis
  - Three lines: new leads, calls made, conversions
  - Dropdown to change time range (7 / 14 / 30 / 60 / 90 days)

- **Task Compliance** (from `GET /reports/tasks/compliance`):
  - Donut chart: on-time vs overdue completion rates

### 9.16 — Admin: CSV History (`/admin/csv-history`)

- Table from `GET /csv/history`
- Columns: file_name, uploaded_by, status badge, total_rows, success/fail/duplicate counts, created_at
- Click row → expand to show error_details

---

## 10 — Shared Components Detail

### `data-table.tsx`
Generic table built on shadcn `Table` with:
- Column definitions (header, accessor, cell renderer)
- Sortable column headers (client-side)
- Row selection with checkboxes
- Loading skeleton state
- Empty state component

### `pagination.tsx`
- Page number buttons + prev/next
- Reads `total_pages` from `PaginatedResponse`
- Emits `onPageChange(page)`

### `page-header.tsx`
- Title, optional description, action buttons slot

### `search-input.tsx`
- Debounced input (300ms) with search icon
- Emits `onSearch(query)`

### `confirm-dialog.tsx`
- shadcn `AlertDialog` wrapper
- Title, description, confirm/cancel buttons
- `onConfirm` callback
- Destructive variant for deletes

---

## 11 — Notification Polling Hook

```ts
// src/hooks/use-notifications.ts
import { useEffect } from "react";
import { useNotificationStore } from "@/stores/notification-store";
import api from "@/lib/api";

export function useNotificationPolling(intervalMs = 30000) {
  const setUnreadCount = useNotificationStore((s) => s.setUnreadCount);

  useEffect(() => {
    const poll = async () => {
      try {
        const { data } = await api.get<{ count: number }>(
          "/notifications/unread-count"
        );
        setUnreadCount(data.count);
      } catch {
        // silent
      }
    };
    poll();
    const id = setInterval(poll, intervalMs);
    return () => clearInterval(id);
  }, [intervalMs, setUnreadCount]);
}
```

---

## 12 — Role-Based Rendering Pattern

```tsx
// Usage in any component
import { useAuthStore } from "@/stores/auth-store";

function SomeComponent() {
  const isAdmin = useAuthStore((s) => s.isAdmin);

  return (
    <div>
      {/* Always visible */}
      <LeadTable />

      {/* Admin only */}
      {isAdmin && <BulkAssignButton />}
    </div>
  );
}
```

For route-level protection, create a wrapper component:

```tsx
// src/components/shared/admin-guard.tsx
"use client";
import { useAuthStore } from "@/stores/auth-store";
import { redirect } from "next/navigation";
import { useEffect } from "react";

export function AdminGuard({ children }: { children: React.ReactNode }) {
  const { isAdmin, isLoading } = useAuthStore();

  useEffect(() => {
    if (!isLoading && !isAdmin) redirect("/leads");
  }, [isAdmin, isLoading]);

  if (isLoading) return <LoadingSkeleton />;
  if (!isAdmin) return null;
  return <>{children}</>;
}
```

Wrap all `/admin/*` page layouts with `<AdminGuard>`.

---

## 13 — Lead Assign Dialog (`lead-assign-dialog.tsx`)

```
- Fetch agents: GET /users?role=agent&is_active=true
- Agent select dropdown with search (shadcn Command / Combobox)
- On confirm: POST /leads/{lead_id}/assign  body: { agent_id }
- Toast on success, refetch lead data
```

### Bulk Assign Dialog (`bulk-assign-dialog.tsx`)

```
- Same agent select as above
- Shows count of selected leads
- On confirm: POST /leads/bulk-assign  body: { lead_ids: [...], agent_id }
- Toast with result count, clear selection, refetch table
```

---

## 14 — Error Handling

- All API errors caught by Axios interceptor for 401
- For other errors, use `try/catch` in each mutation and show `toast.error(error.response?.data?.detail || "Something went wrong")`
- FastAPI returns errors as `{ "detail": "message" }` — parse that
- Form validation: use client-side validation (required fields) + show server validation errors inline

---

## 15 — Loading & Empty States

- Use shadcn `Skeleton` for table loading (match column count)
- Use `empty-state.tsx` component with illustration/icon, title, description, optional CTA button
- Examples:
  - "No leads found" + "Create your first lead" button
  - "No tasks for today" + calendar icon
  - "No notifications" + bell icon

---

## 16 — Colour & Design Tokens

Use these consistent stage colours everywhere (badges, pipeline columns, charts):

| Stage | Tailwind Class | Hex |
|-------|---------------|-----|
| lead | `bg-slate-100 text-slate-700` | Neutral |
| called | `bg-blue-100 text-blue-700` | Info |
| connected | `bg-yellow-100 text-yellow-700` | Warning |
| qualified_lead | `bg-purple-100 text-purple-700` | Purple |
| won | `bg-green-100 text-green-700` | Success |
| lost | `bg-red-100 text-red-700` | Danger |

---

## 17 — API Endpoint → Component Mapping (All 47 Endpoints)

| # | Method | Endpoint | Used In |
|---|--------|----------|---------|
| 1 | GET | `/health` | — (ops only) |
| 2 | POST | `/auth/login` | Login page |
| 3 | POST | `/auth/register` | Admin → Users → Register dialog |
| 4 | POST | `/auth/refresh` | Axios interceptor (auto) |
| 5 | POST | `/auth/logout` | Topbar → Logout |
| 6 | POST | `/auth/reset-password` | Reset password page |
| 7 | PUT | `/auth/update-password` | Update password page |
| 8 | GET | `/users/me` | Auth store → fetchMe |
| 9 | PUT | `/users/me` | Profile settings page |
| 10 | GET | `/users` | Admin users page, agent dropdowns |
| 11 | GET | `/users/{id}` | Admin user detail |
| 12 | PUT | `/users/{id}` | Admin user edit |
| 13 | DELETE | `/users/{id}` | Admin user deactivate |
| 14 | GET | `/users/{id}/stats` | Admin user detail → stats card |
| 15 | GET | `/leads` | Leads list page, pipeline board |
| 16 | POST | `/leads` | Lead form (create) |
| 17 | GET | `/leads/search` | Topbar search, lead table search |
| 18 | GET | `/leads/{id}` | Lead detail page |
| 19 | PUT | `/leads/{id}` | Lead form (edit) |
| 20 | DELETE | `/leads/{id}` | Lead detail → delete (admin) |
| 21 | GET | `/leads/{id}/timeline` | Lead detail → Timeline tab |
| 22 | GET | `/leads/{id}/calls` | Lead detail → Calls tab |
| 23 | GET | `/leads/{id}/tasks` | Lead detail → Tasks tab |
| 24 | POST | `/leads/{id}/assign` | Lead assign dialog |
| 25 | POST | `/leads/bulk-assign` | Bulk assign dialog |
| 26 | GET | `/leads/sources/list` | Source filter dropdown, CSV wizard |
| 27 | POST | `/leads/sources` | Admin sources page |
| 28 | POST | `/leads/{id}/stage` | Stage change dialog, pipeline drag |
| 29 | GET | `/leads/{id}/stage-history` | Lead detail → Timeline tab |
| 30 | POST | `/leads/{id}/calls` | Call log form dialog |
| 31 | GET | `/tasks` | Tasks page (all filter) |
| 32 | POST | `/tasks` | Task form (create) |
| 33 | GET | `/tasks/today` | Tasks page (today filter) |
| 34 | GET | `/tasks/overdue` | Tasks page (overdue filter) |
| 35 | GET | `/tasks/completed-today` | Tasks page (completed filter) |
| 36 | GET | `/tasks/{id}` | Task detail (inline or modal) |
| 37 | PUT | `/tasks/{id}` | Task form (edit) |
| 38 | POST | `/tasks/{id}/complete` | Task complete dialog |
| 39 | GET | `/notifications` | Notifications page |
| 40 | GET | `/notifications/unread-count` | Notification bell polling |
| 41 | PUT | `/notifications/{id}/read` | Notification list → mark read |
| 42 | PUT | `/notifications/read-all` | Notification page → mark all |
| 43 | POST | `/csv/upload` | CSV wizard step 1 |
| 44 | POST | `/csv/{id}/preview` | CSV wizard step 2 |
| 45 | POST | `/csv/{id}/process` | CSV wizard step 3 |
| 46 | GET | `/csv/{id}/status` | CSV wizard step 3 (polling) |
| 47 | GET | `/csv/history` | Admin CSV history page |
| 48 | GET | `/csv/template` | CSV wizard → download template |
| 49 | GET | `/webhooks/meta` | — (backend only) |
| 50 | POST | `/webhooks/meta` | — (backend only) |
| 51 | GET | `/reports/dashboard` | Admin reports → KPI cards |
| 52 | GET | `/reports/pipeline` | Admin reports → pipeline chart |
| 53 | GET | `/reports/agents` | Admin reports → agent table |
| 54 | GET | `/reports/agents/{id}` | Admin reports → agent detail |
| 55 | GET | `/reports/sources` | Admin reports → source chart |
| 56 | GET | `/reports/tasks/compliance` | Admin reports → compliance chart |
| 57 | GET | `/reports/trends` | Admin reports → trends chart |

---

## 18 — Key UX Patterns

1. **Optimistic updates** for stage changes and task completion — update UI immediately, rollback on error.
2. **Debounced search** (300ms) for lead search in topbar and tables.
3. **Skeleton loading** for every data-fetching component.
4. **Toast notifications** (via `sonner`) for all mutations: success = green, error = red.
5. **Confirm dialogs** before all destructive actions (delete lead, deactivate user).
6. **Responsive design**: sidebar collapses to sheet on mobile, tables become card lists on small screens.
7. **Keyboard shortcuts**: `Ctrl+K` → global search, `Escape` → close modals.
8. **URL state** for filters: sync filter values with URL search params so links are shareable.

---

## 19 — Build Order (Recommended)

1. Project setup, shadcn init, install deps
2. Types, constants, API client, Supabase client
3. Auth store, middleware, login page
4. Dashboard layout (sidebar + topbar)
5. Leads list page with filters + pagination
6. Lead detail page with all tabs
7. Pipeline board (Kanban)
8. Tasks page
9. Notifications (bell + page)
10. CSV import wizard
11. Admin: Users management
12. Admin: Sources management
13. Admin: Reports dashboard
14. Profile settings
15. Polish: loading states, empty states, error handling, responsive

---

*This prompt covers every endpoint, model, component, page, and interaction for the complete Admitverse CRM frontend. Follow the build order and component structure for a production-ready implementation.*
