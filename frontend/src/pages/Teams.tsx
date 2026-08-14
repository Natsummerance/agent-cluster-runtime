import { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, message } from 'antd';
import { DeleteOutlined, PlusOutlined, ReloadOutlined, UserAddOutlined } from '@ant-design/icons';
import * as api from '../api/endpoints';
import { apiErrorMessage } from '../store/appStore';
import { useIntl } from '../i18n';
import PageHeader from '../components/PageHeader';
import type { RbacTeam, RbacUser } from '../api/types';

export default function Teams() {
  const intl = useIntl();
  const [teams, setTeams] = useState<RbacTeam[]>([]);
  const [users, setUsers] = useState<RbacUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [teamData, userData] = await Promise.all([api.fetchTeams(), api.fetchUsers()]);
      setTeams(teamData.teams);
      setUsers(userData.users);
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleCreate = useCallback(async () => {
    const values = await form.validateFields();
    setCreating(true);
    try {
      await api.createTeam({ id: values.id.trim(), name: values.name.trim() });
      message.success(intl.formatMessage({ id: 'teams.created', defaultMessage: 'Team created' }));
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
    async (teamId: string) => {
      try {
        await api.deleteTeam(teamId);
        message.success(intl.formatMessage({ id: 'teams.deleted', defaultMessage: 'Team removed' }));
        void load();
      } catch (err) {
        message.error(apiErrorMessage(err));
      }
    },
    [load, intl],
  );

  const handleAddMember = useCallback(
    async (teamId: string, userId: string) => {
      if (!userId) return;
      try {
        await api.updateTeamMembers(teamId, 'add', userId);
        message.success(intl.formatMessage({ id: 'teams.memberAdded', defaultMessage: 'Member added' }));
        void load();
      } catch (err) {
        message.error(apiErrorMessage(err));
      }
    },
    [load, intl],
  );

  const handleRemoveMember = useCallback(
    async (teamId: string, userId: string) => {
      try {
        await api.updateTeamMembers(teamId, 'remove', userId);
        message.success(intl.formatMessage({ id: 'teams.memberRemoved', defaultMessage: 'Member removed' }));
        void load();
      } catch (err) {
        message.error(apiErrorMessage(err));
      }
    },
    [load, intl],
  );

  const userName = useMemo(() => {
    const map = new Map(users.map((u) => [u.id, u.name]));
    return (id: string) => map.get(id) ?? id;
  }, [users]);

  const columns = useMemo(
    () => [
      { title: intl.formatMessage({ id: 'teams.col.id', defaultMessage: 'ID' }), dataIndex: 'id', render: (v: string) => <span className="mono">{v}</span> },
      { title: intl.formatMessage({ id: 'teams.col.name', defaultMessage: 'Name' }), dataIndex: 'name' },
      {
        title: intl.formatMessage({ id: 'teams.col.members', defaultMessage: 'Members' }),
        dataIndex: 'member_ids',
        render: (memberIds: string[], record: RbacTeam) => (
          <Space size={4} wrap>
            {(memberIds ?? []).map((uid) => (
              <Tag key={uid} closable onClose={() => void handleRemoveMember(record.id, uid)} data-testid={`remove-member-${record.id}-${uid}`}>
                {userName(uid)}
              </Tag>
            ))}
            <Select
              size="small"
              placeholder={intl.formatMessage({ id: 'teams.memberAddPlaceholder', defaultMessage: '+ member' })}
              style={{ minWidth: 110 }}
              options={users
                .filter((u) => !(memberIds ?? []).includes(u.id))
                .map((u) => ({ label: `${u.name} (${u.id})`, value: u.id }))}
              value={null}
              onChange={(uid: string) => void handleAddMember(record.id, uid)}
              data-testid={`team-member-add-${record.id}`}
            />
          </Space>
        ),
      },
      {
        title: '',
        key: 'actions',
        render: (_: unknown, record: RbacTeam) => (
          <Popconfirm
            title={intl.formatMessage({ id: 'teams.deleteConfirm', defaultMessage: 'Remove this team?' })}
            onConfirm={() => handleDelete(record.id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} data-testid={`delete-team-${record.id}`} />
          </Popconfirm>
        ),
      },
    ],
    [intl, userName, handleAddMember, handleRemoveMember, handleDelete],
  );

  return (
    <div data-testid="teams-page">
      <PageHeader
        title={intl.formatMessage({ id: 'teams.header.title', defaultMessage: 'Teams' })}
        description={intl.formatMessage({ id: 'teams.header.desc', defaultMessage: 'Group users into teams for shared access' })}
        actions={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => void load()} data-testid="refresh-teams-btn" />
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)} data-testid="add-team-btn">
              {intl.formatMessage({ id: 'teams.create', defaultMessage: 'New team' })}
            </Button>
          </Space>
        }
      />
      <Card>
        <Table<RbacTeam>
          rowKey="id"
          columns={columns}
          dataSource={teams}
          loading={loading}
          pagination={false}
          locale={{ emptyText: intl.formatMessage({ id: 'teams.empty', defaultMessage: 'No teams' }) }}
          data-testid="teams-table"
        />
      </Card>
      <Modal
        title={intl.formatMessage({ id: 'teams.modal.title', defaultMessage: 'New team' })}
        open={modalOpen}
        onOk={handleCreate}
        confirmLoading={creating}
        onCancel={() => setModalOpen(false)}
        okText={intl.formatMessage({ id: 'common.create', defaultMessage: 'Create' })}
        cancelText={intl.formatMessage({ id: 'common.cancel', defaultMessage: 'Cancel' })}
        destroyOnHidden
        data-testid="create-team-modal"
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="id"
            label={intl.formatMessage({ id: 'teams.modal.id', defaultMessage: 'Team ID' })}
            rules={[{ required: true, message: intl.formatMessage({ id: 'teams.modal.idRequired', defaultMessage: 'Please enter the team ID' }) }]}
          >
            <Input data-testid="team-id-input" />
          </Form.Item>
          <Form.Item
            name="name"
            label={intl.formatMessage({ id: 'teams.modal.name', defaultMessage: 'Team name' })}
            rules={[{ required: true, message: intl.formatMessage({ id: 'teams.modal.nameRequired', defaultMessage: 'Please enter the team name' }) }]}
          >
            <Input data-testid="team-name-input" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
