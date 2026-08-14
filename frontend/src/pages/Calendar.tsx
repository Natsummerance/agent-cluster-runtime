import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Button,
  Calendar,
  Card,
  DatePicker,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  message,
} from 'antd';
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import type { Availability, RbacRole } from '../api/types';

const RANGE_FORMAT = 'YYYY-MM-DD HH:mm';

export default function CalendarPage() {
  const intl = useIntl();
  const [availability, setAvailability] = useState<Availability[]>([]);
  const [roles, setRoles] = useState<RbacRole[]>([]);
  const [roleFilter, setRoleFilter] = useState<string | undefined>(undefined);
  const [month, setMonth] = useState<Dayjs>(() => dayjs().startOf('month'));
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();

  const roleName = useCallback(
    (roleId: string) => roles.find((role) => role.id === roleId)?.name ?? roleId,
    [roles],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const from = month.startOf('month');
      const to = month.add(1, 'month').startOf('month');
      const { availability: items } = await api.fetchCalendar({
        role_id: roleFilter || undefined,
        from: from.toISOString(),
        to: to.toISOString(),
      });
      setAvailability(items);
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [month, roleFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    api
      .fetchRoles()
      .then(({ roles: list }) => setRoles(list))
      .catch(() => {
        // 岗位目录加载失败不阻断日历；表格以 role_id 兜底展示
      });
  }, []);

  const handleCreate = useCallback(async () => {
    let values: { role_id?: string; range?: [Dayjs, Dayjs]; note?: string };
    try {
      values = await form.validateFields();
    } catch {
      return; // 校验失败：antd 已在表单项内提示
    }
    setCreating(true);
    try {
      await api.createAvailability({
        role_id: String(values.role_id ?? ''),
        start: values.range![0].toISOString(),
        end: values.range![1].toISOString(),
        note: String(values.note ?? ''),
      });
      message.success(intl.formatMessage({ id: 'calendar.created', defaultMessage: 'Availability created' }));
      setModalOpen(false);
      form.resetFields();
      void load();
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setCreating(false);
    }
  }, [form, load, intl]);

  const handleDelete = useCallback(
    async (availabilityId: string) => {
      try {
        await api.deleteAvailability(availabilityId);
        message.success(intl.formatMessage({ id: 'calendar.deleted', defaultMessage: 'Availability removed' }));
        void load();
      } catch (err) {
        message.error(apiErrorMessage(err));
      }
    },
    [load, intl],
  );

  const blocksByDay = useMemo(() => {
    const map = new Map<string, Availability[]>();
    for (const item of availability) {
      const start = dayjs(item.start);
      const end = dayjs(item.end);
      let cursor = start.startOf('day');
      while (cursor.isBefore(end)) {
        const key = cursor.format('YYYY-MM-DD');
        const list = map.get(key) ?? [];
        list.push(item);
        map.set(key, list);
        cursor = cursor.add(1, 'day');
      }
    }
    return map;
  }, [availability]);

  const renderCell = useCallback(
    (date: Dayjs, info: { type: string }) => {
      if (info.type !== 'date') return null;
      const items = blocksByDay.get(date.format('YYYY-MM-DD'));
      if (!items || items.length === 0) return null;
      return (
        <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
          {items.map((item) => (
            <li key={item.id}>
              <Tag color="blue" data-testid={`availability-tag-${item.id}`}>
                {roleName(item.role_id)}
              </Tag>
            </li>
          ))}
        </ul>
      );
    },
    [blocksByDay, roleName],
  );

  const columns = useMemo(
    () => [
      {
        title: intl.formatMessage({ id: 'calendar.col.role', defaultMessage: 'Role' }),
        dataIndex: 'role_id',
        render: (value: string) => roleName(value),
      },
      {
        title: intl.formatMessage({ id: 'calendar.col.start', defaultMessage: 'Start' }),
        dataIndex: 'start',
        render: (value: string) => new Date(value).toLocaleString(),
      },
      {
        title: intl.formatMessage({ id: 'calendar.col.end', defaultMessage: 'End' }),
        dataIndex: 'end',
        render: (value: string) => new Date(value).toLocaleString(),
      },
      {
        title: intl.formatMessage({ id: 'calendar.col.note', defaultMessage: 'Note' }),
        dataIndex: 'note',
        render: (value?: string) => value || '-',
      },
      {
        title: '',
        key: 'actions',
        render: (_: unknown, record: Availability) => (
          <Popconfirm
            title={intl.formatMessage({
              id: 'calendar.deleteConfirm',
              defaultMessage: 'Remove this availability block?',
            })}
            onConfirm={() => handleDelete(record.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} data-testid={`delete-availability-${record.id}`} />
          </Popconfirm>
        ),
      },
    ],
    [intl, roleName, handleDelete],
  );

  const roleOptions = useMemo(
    () => roles.map((role) => ({ value: role.id, label: role.name })),
    [roles],
  );

  return (
    <div data-testid="calendar-page">
      <PageHeader
        title={intl.formatMessage({ id: 'calendar.header.title', defaultMessage: 'Resource Calendar' })}
        description={intl.formatMessage({
          id: 'calendar.header.desc',
          defaultMessage: 'Role x timeslot availability with overlap conflict checks',
        })}
        actions={
          <Space>
            <Select
              data-testid="calendar-role-filter"
              allowClear
              placeholder={intl.formatMessage({ id: 'calendar.filterRole', defaultMessage: 'All roles' })}
              style={{ width: 220 }}
              value={roleFilter}
              onChange={(value?: string) => setRoleFilter(value || undefined)}
              options={roleOptions}
              virtual={false}
            />
            <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}>
              {intl.formatMessage({ id: 'common.refresh', defaultMessage: 'Refresh' })}
            </Button>
            <Button
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setModalOpen(true)}
              data-testid="add-availability-btn"
            >
              {intl.formatMessage({ id: 'calendar.add', defaultMessage: 'New availability' })}
            </Button>
          </Space>
        }
      />
      <Card loading={loading} data-testid="calendar-grid">
        <Calendar
          value={month}
          onPanelChange={(date) => setMonth(date.startOf('month'))}
          cellRender={renderCell}
          fullscreen
        />
      </Card>
      <Card
        style={{ marginTop: 16 }}
        title={intl.formatMessage({ id: 'calendar.table.title', defaultMessage: 'Availability blocks' })}
      >
        <Table
          rowKey="id"
          data-testid="availability-table"
          dataSource={availability}
          columns={columns}
          pagination={false}
          size="small"
        />
      </Card>
      <Modal
        open={modalOpen}
        onCancel={() => {
          setModalOpen(false);
          form.resetFields();
        }}
        onOk={() => void handleCreate()}
        confirmLoading={creating}
        title={intl.formatMessage({ id: 'calendar.modal.title', defaultMessage: 'New availability block' })}
        data-testid="create-availability-modal"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="role_id"
            label={intl.formatMessage({ id: 'calendar.modal.role', defaultMessage: 'Role' })}
            rules={[{ required: true, message: intl.formatMessage({ id: 'calendar.modal.roleRequired', defaultMessage: 'Please select a role' }) }]}
          >
            <Select
              data-testid="availability-role-select"
              placeholder={intl.formatMessage({ id: 'calendar.modal.rolePlaceholder', defaultMessage: 'Select a role' })}
              options={roleOptions}
              virtual={false}
            />
          </Form.Item>
          <Form.Item
            name="range"
            label={intl.formatMessage({ id: 'calendar.modal.range', defaultMessage: 'Timeslot' })}
            rules={[{ required: true, message: intl.formatMessage({ id: 'calendar.modal.rangeRequired', defaultMessage: 'Please pick a timeslot' }) }]}
          >
            <DatePicker.RangePicker
              data-testid="availability-range-picker"
              showTime
              format={RANGE_FORMAT}
              style={{ width: '100%' }}
            />
          </Form.Item>
          <Form.Item
            name="note"
            label={intl.formatMessage({ id: 'calendar.modal.note', defaultMessage: 'Note' })}
          >
            <Input
              data-testid="availability-note-input"
              placeholder={intl.formatMessage({ id: 'calendar.modal.notePlaceholder', defaultMessage: 'Optional remark' })}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}