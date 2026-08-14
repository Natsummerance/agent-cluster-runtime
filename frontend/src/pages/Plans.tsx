import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Button,
  Card,
  DatePicker,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  message,
} from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import type { Goal, Job, Plan, PlanDetailData, Schedule } from '../api/types';

const GOAL_STATUS_OPTIONS = ['active', 'paused', 'complete'];
const SCHEDULE_KINDS = ['at', 'after', 'every'];

export default function PlansPage() {
  const intl = useIntl();
  const [plans, setPlans] = useState<Plan[]>([]);
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState<string | undefined>(undefined);
  const [detail, setDetail] = useState<PlanDetailData | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [planModalOpen, setPlanModalOpen] = useState(false);
  const [creatingPlan, setCreatingPlan] = useState(false);
  const [goalModalOpen, setGoalModalOpen] = useState(false);
  const [creatingGoal, setCreatingGoal] = useState(false);
  const [settleJobId, setSettleJobId] = useState<string | undefined>(undefined);
  const [settling, setSettling] = useState(false);
  const [scheduleModalOpen, setScheduleModalOpen] = useState(false);
  const [creatingSchedule, setCreatingSchedule] = useState(false);
  const [planForm] = Form.useForm();
  const [goalForm] = Form.useForm();
  const [settleForm] = Form.useForm();
  const [scheduleForm] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { plans: list } = await api.fetchPlans();
      setPlans(list);
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadSchedules = useCallback(async () => {
    try {
      const { schedules: list } = await api.fetchSchedules();
      setSchedules(list);
    } catch (err) {
      message.error(apiErrorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
    void loadSchedules();
  }, [load, loadSchedules]);

  const loadDetail = useCallback(async (planId: string) => {
    setDetailLoading(true);
    try {
      const data = await api.fetchPlan(planId);
      setDetail(data);
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedPlanId) {
      void loadDetail(selectedPlanId);
    } else {
      setDetail(null);
    }
  }, [selectedPlanId, loadDetail]);

  const handleCreatePlan = useCallback(async () => {
    let values: { name?: string; mode?: string };
    try {
      values = await planForm.validateFields();
    } catch {
      return; // 校验失败：antd 已在表单项内提示
    }
    setCreatingPlan(true);
    try {
      const { plan } = await api.createPlan({
        name: String(values.name ?? ''),
        mode: String(values.mode ?? 'inactive'),
      });
      message.success(intl.formatMessage({ id: 'plans.created', defaultMessage: 'Plan created' }));
      setPlanModalOpen(false);
      planForm.resetFields();
      void load();
      setSelectedPlanId(plan.id);
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setCreatingPlan(false);
    }
  }, [planForm, load, intl]);

  const handleCreateGoal = useCallback(async () => {
    if (!selectedPlanId) return;
    let values: { objective?: string; max_rounds?: number };
    try {
      values = await goalForm.validateFields();
    } catch {
      return;
    }
    setCreatingGoal(true);
    try {
      await api.createGoal(selectedPlanId, {
        objective: String(values.objective ?? ''),
        max_rounds: values.max_rounds,
      });
      message.success(intl.formatMessage({ id: 'plans.goal.created', defaultMessage: 'Goal created' }));
      setGoalModalOpen(false);
      goalForm.resetFields();
      void loadDetail(selectedPlanId);
      void load();
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setCreatingGoal(false);
    }
  }, [selectedPlanId, goalForm, loadDetail, load, intl]);

  const handleStartRound = useCallback(
    async (goal: Goal) => {
      try {
        await api.changeGoal(goal.id, {
          expected_version: goal.version ?? 1,
          start_round: true,
        });
        message.success(intl.formatMessage({ id: 'plans.goal.roundStarted', defaultMessage: 'Round started' }));
        if (selectedPlanId) void loadDetail(selectedPlanId);
      } catch (err) {
        message.error(apiErrorMessage(err));
      }
    },
    [selectedPlanId, loadDetail, intl],
  );

  const handleStatusChange = useCallback(
    async (goal: Goal, status: string) => {
      try {
        await api.changeGoal(goal.id, {
          expected_version: goal.version ?? 1,
          status,
        });
        message.success(
          intl.formatMessage({ id: 'plans.goal.statusChanged', defaultMessage: 'Goal status updated' }),
        );
        if (selectedPlanId) void loadDetail(selectedPlanId);
      } catch (err) {
        message.error(apiErrorMessage(err));
      }
    },
    [selectedPlanId, loadDetail, intl],
  );

  const handleSettle = useCallback(async () => {
    if (!settleJobId) return;
    let values: { outcome?: string };
    try {
      values = await settleForm.validateFields();
    } catch {
      return;
    }
    setSettling(true);
    try {
      await api.settleJob(settleJobId, String(values.outcome ?? ''));
      message.success(intl.formatMessage({ id: 'plans.job.settled', defaultMessage: 'Job settled' }));
      setSettleJobId(undefined);
      settleForm.resetFields();
      if (selectedPlanId) void loadDetail(selectedPlanId);
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setSettling(false);
    }
  }, [settleJobId, settleForm, selectedPlanId, loadDetail, intl]);

  const handleCreateSchedule = useCallback(async () => {
    let values: { kind?: string; at?: Dayjs; minutes?: number };
    try {
      values = await scheduleForm.validateFields();
    } catch {
      return;
    }
    const kind = String(values.kind ?? 'at');
    setCreatingSchedule(true);
    try {
      await api.createSchedule({
        kind,
        at: kind === 'at' ? values.at!.toISOString() : undefined,
        after_minutes: kind === 'after' ? Number(values.minutes) : undefined,
        every_minutes: kind === 'every' ? Number(values.minutes) : undefined,
      });
      message.success(intl.formatMessage({ id: 'plans.schedule.created', defaultMessage: 'Schedule created' }));
      setScheduleModalOpen(false);
      scheduleForm.resetFields();
      void loadSchedules();
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setCreatingSchedule(false);
    }
  }, [scheduleForm, loadSchedules, intl]);

  const scheduleKind = Form.useWatch('kind', scheduleForm);

  const statusKey = useCallback((status?: string) => {
    const value = status ?? 'active';
    return `plans.goal.status${value.charAt(0).toUpperCase()}${value.slice(1)}`;
  }, []);

  const goalStatusLabel = useCallback(
    (status?: string) =>
      intl.formatMessage({ id: statusKey(status), defaultMessage: status ?? 'active' }),
    [intl, statusKey],
  );

  const jobStateKey = useCallback((state?: string) => {
    const value = state ?? 'pending';
    return `plans.job.state${value.charAt(0).toUpperCase()}${value.slice(1)}`;
  }, []);

  const jobStateLabel = useCallback(
    (state?: string) =>
      intl.formatMessage({ id: jobStateKey(state), defaultMessage: state ?? 'pending' }),
    [intl, jobStateKey],
  );

  const planColumns = useMemo(
    () => [
      {
        title: intl.formatMessage({ id: 'plans.col.id', defaultMessage: 'Plan' }),
        dataIndex: 'id',
        render: (value: string) => <Tag color="purple">{value}</Tag>,
      },
      {
        title: intl.formatMessage({ id: 'plans.col.name', defaultMessage: 'Name' }),
        dataIndex: 'name',
        render: (value?: string) => value || '-',
      },
      {
        title: intl.formatMessage({ id: 'plans.col.mode', defaultMessage: 'Mode' }),
        dataIndex: 'mode',
        render: (value?: string) => (
          <Tag color={value === 'active' ? 'green' : 'default'}>
            {intl.formatMessage({
              id: value === 'active' ? 'plans.mode.active' : 'plans.mode.inactive',
              defaultMessage: value === 'active' ? 'Active' : 'Inactive',
            })}
          </Tag>
        ),
      },
      {
        title: intl.formatMessage({ id: 'plans.col.goals', defaultMessage: 'Goals' }),
        dataIndex: 'goals',
        render: (value?: string[]) => value?.length ?? 0,
      },
      {
        title: intl.formatMessage({ id: 'plans.col.jobs', defaultMessage: 'Jobs' }),
        dataIndex: 'jobs',
        render: (value?: string[]) => value?.length ?? 0,
      },
      {
        title: intl.formatMessage({ id: 'plans.col.created', defaultMessage: 'Created' }),
        dataIndex: 'created_at',
        render: (value?: string) => (value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '-'),
      },
    ],
    [intl],
  );

  const goalColumns = useMemo(
    () => [
      {
        title: intl.formatMessage({ id: 'plans.goal.colId', defaultMessage: 'Goal' }),
        dataIndex: 'id',
        render: (value: string) => <Tag color="blue">{value}</Tag>,
      },
      {
        title: intl.formatMessage({ id: 'plans.goal.colObjective', defaultMessage: 'Objective' }),
        dataIndex: 'objective',
      },
      {
        title: intl.formatMessage({ id: 'plans.goal.colStatus', defaultMessage: 'Status' }),
        dataIndex: 'status',
        render: (value: string, record: Goal) => {
          const current = value ?? 'active';
          const options = [current, ...GOAL_STATUS_OPTIONS.filter((option) => option !== current)];
          return (
            <Select
              size="small"
              virtual={false}
              data-testid={`goal-status-select-${record.id}`}
              style={{ width: 110 }}
              value={current}
              options={options.map((option) => ({
                value: option,
                label: goalStatusLabel(option),
              }))}
              onChange={(next: string) => void handleStatusChange(record, next)}
            />
          );
        },
      },
      {
        title: intl.formatMessage({ id: 'plans.goal.colRounds', defaultMessage: 'Rounds' }),
        dataIndex: 'rounds',
        render: (value?: number, record?: Goal) => `${value ?? 0} / ${record?.max_rounds ?? '-'}`,
      },
      {
        title: intl.formatMessage({ id: 'plans.goal.colVersion', defaultMessage: 'Version' }),
        dataIndex: 'version',
        render: (value?: number) => value ?? 1,
      },
      {
        title: intl.formatMessage({ id: 'plans.goal.colActions', defaultMessage: 'Actions' }),
        key: 'actions',
        render: (_: unknown, record: Goal) => (
          <Button
            size="small"
            disabled={record.status === 'complete'}
            onClick={() => void handleStartRound(record)}
            data-testid={`start-round-${record.id}`}
          >
            {intl.formatMessage({ id: 'plans.goal.startRound', defaultMessage: 'Start round' })}
          </Button>
        ),
      },
    ],
    [intl, goalStatusLabel, handleStatusChange, handleStartRound],
  );

  const jobColumns = useMemo(
    () => [
      {
        title: intl.formatMessage({ id: 'plans.job.colId', defaultMessage: 'Job' }),
        dataIndex: 'id',
        render: (value: string) => <Tag color="orange">{value}</Tag>,
      },
      {
        title: intl.formatMessage({ id: 'plans.job.colOwner', defaultMessage: 'Owner' }),
        dataIndex: 'owner',
      },
      {
        title: intl.formatMessage({ id: 'plans.job.colState', defaultMessage: 'State' }),
        dataIndex: 'state',
        render: (value?: string) => (
          <Tag color={value === 'settled' ? 'green' : value === 'running' ? 'blue' : 'default'}>
            {jobStateLabel(value)}
          </Tag>
        ),
      },
      {
        title: intl.formatMessage({ id: 'plans.job.colOutcome', defaultMessage: 'Outcome' }),
        dataIndex: 'outcome',
        render: (value?: string) => value || '-',
      },
      {
        title: intl.formatMessage({ id: 'plans.job.colActions', defaultMessage: 'Actions' }),
        key: 'actions',
        render: (_: unknown, record: Job) => (
          <Button
            size="small"
            disabled={record.state === 'settled'}
            onClick={() => setSettleJobId(record.id)}
            data-testid={`settle-job-${record.id}`}
          >
            {intl.formatMessage({ id: 'plans.job.settle', defaultMessage: 'Settle' })}
          </Button>
        ),
      },
    ],
    [intl, jobStateLabel],
  );

  const scheduleColumns = useMemo(
    () => [
      {
        title: intl.formatMessage({ id: 'plans.schedule.colId', defaultMessage: 'Schedule' }),
        dataIndex: 'id',
        render: (value: string) => <Tag color="cyan">{value}</Tag>,
      },
      {
        title: intl.formatMessage({ id: 'plans.schedule.colKind', defaultMessage: 'Kind' }),
        dataIndex: 'kind',
        render: (value: string) => (
          <Tag>{intl.formatMessage({ id: `plans.schedule.kind${value.charAt(0).toUpperCase()}${value.slice(1)}`, defaultMessage: value })}</Tag>
        ),
      },
      {
        title: intl.formatMessage({ id: 'plans.schedule.colAt', defaultMessage: 'At' }),
        dataIndex: 'at',
        render: (value?: string | null) => (value ? dayjs(value).format('YYYY-MM-DD HH:mm') : '-'),
      },
      {
        title: intl.formatMessage({ id: 'plans.schedule.colAfter', defaultMessage: 'After (min)' }),
        dataIndex: 'after_minutes',
        render: (value?: number | null) => value ?? '-',
      },
      {
        title: intl.formatMessage({ id: 'plans.schedule.colEvery', defaultMessage: 'Every (min)' }),
        dataIndex: 'every_minutes',
        render: (value?: number | null) => value ?? '-',
      },
      {
        title: intl.formatMessage({ id: 'plans.schedule.colState', defaultMessage: 'State' }),
        dataIndex: 'state',
        render: (value?: string) => <Tag color={value === 'active' ? 'green' : 'default'}>{value ?? 'active'}</Tag>,
      },
    ],
    [intl],
  );

  const scheduleKindOptions = useMemo(
    () =>
      SCHEDULE_KINDS.map((kind) => ({
        value: kind,
        label: intl.formatMessage({ id: `plans.schedule.kind${kind.charAt(0).toUpperCase()}${kind.slice(1)}`, defaultMessage: kind }),
      })),
    [intl],
  );

  return (
    <div data-testid="plans-page">
      <PageHeader
        title={intl.formatMessage({ id: 'plans.header.title', defaultMessage: 'Plans' })}
        description={intl.formatMessage({
          id: 'plans.header.desc',
          defaultMessage: 'Plan / goal / jobs / schedule orchestration',
        })}
        actions={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
              {intl.formatMessage({ id: 'common.refresh', defaultMessage: 'Refresh' })}
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setPlanModalOpen(true)}
              data-testid="add-plan-btn"
            >
              {intl.formatMessage({ id: 'plans.add', defaultMessage: 'New plan' })}
            </Button>
          </Space>
        }
      />
      <Card
        title={intl.formatMessage({ id: 'plans.table.title', defaultMessage: 'Plans' })}
        loading={loading}
        data-testid="plans-card"
      >
        <Table
          rowKey="id"
          data-testid="plans-table"
          dataSource={plans}
          columns={planColumns}
          pagination={false}
          size="small"
          rowClassName={(record: Plan) => (record.id === selectedPlanId ? 'ant-table-row-selected' : '')}
          onRow={(record: Plan) => ({
            onClick: () => setSelectedPlanId(record.id),
            'data-testid': `plan-row-${record.id}`,
          })}
        />
      </Card>
      <Card
        style={{ marginTop: 16 }}
        title={intl.formatMessage({ id: 'plans.goals.title', defaultMessage: 'Goals' })}
        loading={detailLoading}
        extra={
          <Button
            size="small"
            type="primary"
            icon={<PlusOutlined />}
            disabled={!selectedPlanId}
            onClick={() => setGoalModalOpen(true)}
            data-testid="add-goal-btn"
          >
            {intl.formatMessage({ id: 'plans.goals.add', defaultMessage: 'New goal' })}
          </Button>
        }
      >
        {!selectedPlanId || !detail ? (
          <Empty
            description={intl.formatMessage({
              id: 'plans.selectHint',
              defaultMessage: 'Select a plan to view goals and jobs',
            })}
          />
        ) : (
          <Table
            rowKey="id"
            data-testid="goals-table"
            dataSource={detail.goals}
            columns={goalColumns}
            pagination={false}
            size="small"
            locale={{
              emptyText: intl.formatMessage({ id: 'plans.goals.empty', defaultMessage: 'No goals yet' }),
            }}
          />
        )}
      </Card>
      <Card
        style={{ marginTop: 16 }}
        title={intl.formatMessage({ id: 'plans.jobs.title', defaultMessage: 'Jobs' })}
        loading={detailLoading}
        data-testid="jobs-card"
      >
        {!selectedPlanId || !detail ? (
          <Empty
            description={intl.formatMessage({
              id: 'plans.selectHint',
              defaultMessage: 'Select a plan to view goals and jobs',
            })}
          />
        ) : (
          <Table
            rowKey="id"
            data-testid="jobs-table"
            dataSource={detail.jobs}
            columns={jobColumns}
            pagination={false}
            size="small"
            locale={{
              emptyText: intl.formatMessage({ id: 'plans.jobs.empty', defaultMessage: 'No jobs yet' }),
            }}
          />
        )}
      </Card>
      <Card
        style={{ marginTop: 16 }}
        title={intl.formatMessage({ id: 'plans.schedules.title', defaultMessage: 'Schedules' })}
        data-testid="schedules-card"
        extra={
          <Button
            size="small"
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setScheduleModalOpen(true)}
            data-testid="add-schedule-btn"
          >
            {intl.formatMessage({ id: 'plans.schedule.add', defaultMessage: 'New schedule' })}
          </Button>
        }
      >
        <Table
          rowKey="id"
          data-testid="schedules-table"
          dataSource={schedules}
          columns={scheduleColumns}
          pagination={false}
          size="small"
          locale={{
            emptyText: intl.formatMessage({ id: 'plans.schedules.empty', defaultMessage: 'No schedules yet' }),
          }}
        />
      </Card>
      <Modal
        open={planModalOpen}
        onCancel={() => {
          setPlanModalOpen(false);
          planForm.resetFields();
        }}
        onOk={() => void handleCreatePlan()}
        confirmLoading={creatingPlan}
        title={intl.formatMessage({ id: 'plans.modal.title', defaultMessage: 'New plan' })}
        data-testid="create-plan-modal"
      >
        <Form form={planForm} layout="vertical">
          <Form.Item
            name="name"
            label={intl.formatMessage({ id: 'plans.modal.name', defaultMessage: 'Name' })}
          >
            <Input
              data-testid="plan-name-input"
              placeholder={intl.formatMessage({
                id: 'plans.modal.namePlaceholder',
                defaultMessage: 'Optional plan name',
              })}
            />
          </Form.Item>
          <Form.Item
            name="mode"
            label={intl.formatMessage({ id: 'plans.modal.mode', defaultMessage: 'Initial mode' })}
            initialValue="inactive"
          >
            <Select
              data-testid="plan-mode-select"
              virtual={false}
              options={[
                { value: 'inactive', label: intl.formatMessage({ id: 'plans.mode.inactive', defaultMessage: 'Inactive' }) },
                { value: 'active', label: intl.formatMessage({ id: 'plans.mode.active', defaultMessage: 'Active' }) },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        open={goalModalOpen}
        onCancel={() => {
          setGoalModalOpen(false);
          goalForm.resetFields();
        }}
        onOk={() => void handleCreateGoal()}
        confirmLoading={creatingGoal}
        title={intl.formatMessage({ id: 'plans.goal.modalTitle', defaultMessage: 'New goal' })}
        data-testid="create-goal-modal"
      >
        <Form form={goalForm} layout="vertical">
          <Form.Item
            name="objective"
            label={intl.formatMessage({ id: 'plans.goal.modalObjective', defaultMessage: 'Objective' })}
            rules={[
              {
                required: true,
                message: intl.formatMessage({
                  id: 'plans.goal.objectiveRequired',
                  defaultMessage: 'Please enter the objective',
                }),
              },
            ]}
          >
            <Input.TextArea
              data-testid="goal-objective-input"
              rows={3}
              placeholder={intl.formatMessage({
                id: 'plans.goal.modalObjective',
                defaultMessage: 'Objective',
              })}
            />
          </Form.Item>
          <Form.Item
            name="max_rounds"
            label={intl.formatMessage({ id: 'plans.goal.modalMaxRounds', defaultMessage: 'Max rounds (optional)' })}
          >
            <InputNumber data-testid="goal-max-rounds-input" min={1} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        open={settleJobId !== undefined}
        onCancel={() => {
          setSettleJobId(undefined);
          settleForm.resetFields();
        }}
        onOk={() => void handleSettle()}
        confirmLoading={settling}
        title={intl.formatMessage({ id: 'plans.job.modalTitle', defaultMessage: 'Settle job' })}
        data-testid="settle-job-modal"
      >
        <Form form={settleForm} layout="vertical">
          <Form.Item
            name="outcome"
            label={intl.formatMessage({ id: 'plans.job.modalOutcome', defaultMessage: 'Outcome' })}
            rules={[
              {
                required: true,
                message: intl.formatMessage({
                  id: 'plans.job.outcomeRequired',
                  defaultMessage: 'Please enter the outcome',
                }),
              },
            ]}
          >
            <Input
              data-testid="job-outcome-input"
              placeholder={intl.formatMessage({
                id: 'plans.job.modalOutcome',
                defaultMessage: 'Outcome',
              })}
            />
          </Form.Item>
        </Form>
      </Modal>
      <Modal
        open={scheduleModalOpen}
        onCancel={() => {
          setScheduleModalOpen(false);
          scheduleForm.resetFields();
        }}
        onOk={() => void handleCreateSchedule()}
        confirmLoading={creatingSchedule}
        title={intl.formatMessage({ id: 'plans.schedule.modalTitle', defaultMessage: 'New schedule' })}
        data-testid="create-schedule-modal"
      >
        <Form form={scheduleForm} layout="vertical" initialValues={{ kind: 'at' }}>
          <Form.Item
            name="kind"
            label={intl.formatMessage({ id: 'plans.schedule.modalKind', defaultMessage: 'Kind' })}
            rules={[
              {
                required: true,
                message: intl.formatMessage({
                  id: 'plans.schedule.kindRequired',
                  defaultMessage: 'Please select a kind',
                }),
              },
            ]}
          >
            <Select
              data-testid="schedule-kind-select"
              virtual={false}
              options={scheduleKindOptions}
            />
          </Form.Item>
          {scheduleKind === 'at' && (
            <Form.Item
              name="at"
              label={intl.formatMessage({ id: 'plans.schedule.modalAt', defaultMessage: 'Run at' })}
              rules={[
                {
                  required: true,
                  message: intl.formatMessage({
                    id: 'plans.schedule.atRequired',
                    defaultMessage: 'Please pick a time',
                  }),
                },
              ]}
            >
              <DatePicker
                data-testid="schedule-at-picker"
                showTime
                style={{ width: '100%' }}
                disabledDate={(current) => current && current.isBefore(dayjs().startOf('day'))}
              />
            </Form.Item>
          )}
          {(scheduleKind === 'after' || scheduleKind === 'every') && (
            <Form.Item
              name="minutes"
              label={intl.formatMessage({ id: 'plans.schedule.modalMinutes', defaultMessage: 'Minutes' })}
              rules={[
                {
                  required: true,
                  message: intl.formatMessage({
                    id: 'plans.schedule.minutesRequired',
                    defaultMessage: 'Please enter minutes (at least 5)',
                  }),
                },
              ]}
            >
              <InputNumber data-testid="schedule-minutes-input" min={5} style={{ width: '100%' }} />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  );
}
