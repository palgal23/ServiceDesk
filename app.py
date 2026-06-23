from flask import Flask, render_template, redirect, url_for, flash, request, send_file
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import io
import csv

app = Flask(__name__)
app.config['SECRET_KEY'] = 'super-secret-key-for-diploma-2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///servicedesk.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Пожалуйста, войдите в систему.'


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    created_requests = db.relationship('Request', foreign_keys='Request.dispatcher_id', backref='dispatcher_obj', lazy=True)
    assigned_requests = db.relationship('Request', foreign_keys='Request.engineer_id', backref='engineer_obj', lazy=True)
    action_logs = db.relationship('ActionLog', backref='user_obj', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Client(db.Model):
    __tablename__ = 'clients'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    contact_person = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(300), nullable=True)
    contract_number = db.Column(db.String(50), nullable=True)
    is_active = db.Column(db.Boolean, default=True)

    requests = db.relationship('Request', backref='client_obj', lazy=True)


class SLAConfig(db.Model):
    __tablename__ = 'sla_config'
    id = db.Column(db.Integer, primary_key=True)
    priority = db.Column(db.String(10), unique=True, nullable=False)
    reaction_hours = db.Column(db.Float, nullable=False, default=2)
    solution_hours = db.Column(db.Float, nullable=False, default=8)

    def get_priority_display(self):
        priorities = {'low': 'Низкий', 'medium': 'Средний', 'high': 'Высокий'}
        return priorities.get(self.priority, self.priority)


class Request(db.Model):
    __tablename__ = 'requests'
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.Text, nullable=False)
    priority = db.Column(db.String(10), nullable=False, default='medium')
    status = db.Column(db.String(15), nullable=False, default='new')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    equipment_type = db.Column(db.String(100), nullable=True)
    location = db.Column(db.String(200), nullable=True)
    sla_breached = db.Column(db.Boolean, default=False)

    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    dispatcher_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    engineer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    status_history = db.relationship('StatusHistory', backref='request_obj', lazy=True)

    def get_status_display(self):
        statuses = {'new': 'Новая', 'in_progress': 'В работе', 'resolved': 'Решена', 'closed': 'Закрыта'}
        return statuses.get(self.status, self.status)

    def get_priority_display(self):
        priorities = {'low': 'Низкий', 'medium': 'Средний', 'high': 'Высокий'}
        return priorities.get(self.priority, self.priority)

    def get_sla_config(self):
        return SLAConfig.query.filter_by(priority=self.priority).first()

    def get_sla_info(self):
        sla_config = self.get_sla_config()
        if sla_config:
            reaction_hours = sla_config.reaction_hours
            solution_hours = sla_config.solution_hours
        else:
            defaults = {'high': (0.5, 2), 'medium': (2, 8), 'low': (8, 24)}
            reaction_hours, solution_hours = defaults.get(self.priority, (2, 8))

        now = datetime.utcnow()
        reaction_deadline = self.created_at + timedelta(hours=reaction_hours)
        solution_deadline = self.created_at + timedelta(hours=solution_hours)

        if self.status == 'new':
            if now > reaction_deadline:
                reaction_status = 'expired'
            elif now > reaction_deadline - timedelta(hours=reaction_hours * 0.5):
                reaction_status = 'warning'
            else:
                reaction_status = 'ok'
        elif self.status in ['in_progress', 'resolved', 'closed']:
            reaction_status = 'done'
        else:
            reaction_status = 'ok'

        if self.status == 'closed':
            solution_status = 'done'
        elif self.status in ['new', 'in_progress', 'resolved']:
            if now > solution_deadline:
                solution_status = 'expired'
            elif now > solution_deadline - timedelta(hours=solution_hours * 0.5):
                solution_status = 'warning'
            else:
                solution_status = 'ok'
        else:
            solution_status = 'ok'

        return {
            'reaction_status': reaction_status,
            'solution_status': solution_status,
            'reaction_hours': reaction_hours,
            'solution_hours': solution_hours,
            'reaction_deadline': reaction_deadline,
            'solution_deadline': solution_deadline
        }

    def get_sorted_history(self):
        return sorted(self.status_history, key=lambda h: h.changed_at, reverse=True)


class StatusHistory(db.Model):
    __tablename__ = 'status_history'
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('requests.id'), nullable=False)
    old_status = db.Column(db.String(15), nullable=True)
    new_status = db.Column(db.String(15), nullable=False)
    changed_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)
    comment = db.Column(db.Text, nullable=True)

    def get_status_display(self, status):
        statuses = {'new': 'Новая', 'in_progress': 'В работе', 'resolved': 'Решена', 'closed': 'Закрыта'}
        return statuses.get(status, status)


class ActionLog(db.Model):
    __tablename__ = 'action_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    action = db.Column(db.String(200), nullable=False)
    details = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class Notification(db.Model):
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    message = db.Column(db.String(300), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    request_id = db.Column(db.Integer, db.ForeignKey('requests.id'), nullable=True)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def log_action(user, action, details=None):
    log = ActionLog(user_id=user.id, action=action, details=details)
    db.session.add(log)
    db.session.commit()


def check_sla_breaches():
    requests_list = Request.query.filter(Request.status != 'closed').all()
    for req in requests_list:
        sla = req.get_sla_info()
        if sla['solution_status'] == 'expired' and not req.sla_breached:
            req.sla_breached = True
            manager = User.query.filter_by(role='manager').first()
            if manager:
                notif = Notification(
                    user_id=manager.id,
                    message=f'Заявка №{req.id} просрочена! Клиент: {req.client_obj.name}',
                    request_id=req.id
                )
                db.session.add(notif)
            engineer = req.engineer_obj
            if engineer:
                notif2 = Notification(
                    user_id=engineer.id,
                    message=f'Заявка №{req.id} просрочена! Срочно обновите статус.',
                    request_id=req.id
                )
                db.session.add(notif2)
    db.session.commit()


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password) and user.is_active:
            login_user(user)
            log_action(user, 'Вход в систему')
            flash(f'Добро пожаловать, {user.full_name}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Неверный логин, пароль или аккаунт заблокирован', 'danger')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    log_action(current_user, 'Выход из системы')
    logout_user()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('index'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        full_name = request.form.get('full_name')
        role = request.form.get('role')
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            flash('Пользователь с таким логином уже существует', 'danger')
            return redirect(url_for('register'))
        user = User(username=username, full_name=full_name, role=role)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash(f'Пользователь {full_name} успешно создан!', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/create-request', methods=['GET', 'POST'])
@login_required
def create_request():
    if current_user.role != 'dispatcher':
        flash('Только диспетчер может создавать заявки', 'danger')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        client_id = request.form.get('client_id')
        description = request.form.get('description')
        priority = request.form.get('priority')
        engineer_id = request.form.get('engineer_id')
        equipment_type = request.form.get('equipment_type')
        location = request.form.get('location')
        if not all([client_id, description, priority]):
            flash('Заполните все обязательные поля', 'danger')
            return redirect(url_for('create_request'))
        new_request = Request(
            client_id=int(client_id), description=description, priority=priority,
            status='new', dispatcher_id=current_user.id,
            engineer_id=int(engineer_id) if engineer_id else None,
            equipment_type=equipment_type, location=location
        )
        db.session.add(new_request)
        db.session.flush()
        history = StatusHistory(request_id=new_request.id, old_status=None, new_status='new',
                                changed_by=current_user.id, comment='Заявка создана')
        db.session.add(history)
        log_action(current_user, 'Создание заявки', f'Заявка №{new_request.id}, клиент ID={client_id}')
        db.session.commit()
        flash(f'Заявка №{new_request.id} успешно создана!', 'success')
        return redirect(url_for('dashboard'))
    clients = Client.query.filter_by(is_active=True).order_by(Client.name).all()
    engineers = User.query.filter_by(role='engineer', is_active=True).order_by(User.full_name).all()
    return render_template('create_request.html', clients=clients, engineers=engineers)


@app.route('/request/<int:request_id>/change-status', methods=['GET', 'POST'])
@login_required
def change_status(request_id):
    req = db.session.get(Request, request_id)
    if not req:
        flash('Заявка не найдена', 'danger')
        return redirect(url_for('dashboard'))
    if current_user.role == 'manager':
        flash('Руководитель не может менять статусы заявок', 'danger')
        return redirect(url_for('dashboard'))
    if current_user.role == 'engineer' and req.engineer_id != current_user.id:
        flash('Вы можете менять статус только своих заявок', 'danger')
        return redirect(url_for('dashboard'))
    all_statuses = ['new', 'in_progress', 'resolved', 'closed']
    if request.method == 'POST':
        new_status = request.form.get('new_status')
        comment = request.form.get('comment')
        if new_status not in all_statuses:
            flash('Недопустимый статус', 'danger')
            return redirect(url_for('change_status', request_id=request_id))
        old_status = req.status
        req.status = new_status
        req.updated_at = datetime.utcnow()
        if new_status == 'closed':
            req.sla_breached = False
        history = StatusHistory(request_id=req.id, old_status=old_status, new_status=new_status,
                                changed_by=current_user.id, comment=comment if comment else '')
        db.session.add(history)
        log_action(current_user, 'Изменение статуса заявки',
                   f'Заявка №{req.id}: {old_status} -> {new_status}')
        db.session.commit()
        flash(f'Статус заявки №{req.id} изменён: {req.get_status_display()}', 'success')
        return redirect(url_for('dashboard'))
    history_with_users = []
    for h in req.get_sorted_history():
        user = db.session.get(User, h.changed_by)
        history_with_users.append({'history': h, 'user_name': user.full_name if user else 'Неизвестный'})
    return render_template('change_status.html', request=req, all_statuses=all_statuses, history_with_users=history_with_users)


@app.route('/dashboard')
@login_required
def dashboard():
    check_sla_breaches()
    status_filter = request.args.get('status', '')
    priority_filter = request.args.get('priority', '')
    search_query = request.args.get('search', '')

    if current_user.role == 'engineer':
        query = Request.query.filter_by(engineer_id=current_user.id)
    else:
        query = Request.query

    if status_filter:
        query = query.filter_by(status=status_filter)
    if priority_filter:
        query = query.filter_by(priority=priority_filter)
    if search_query:
        query = query.filter(Request.description.contains(search_query))

    requests_list = query.order_by(Request.created_at.desc()).all()

    expired_count = 0
    for r in requests_list:
        sla = r.get_sla_info()
        if r.status != 'closed' and (sla['reaction_status'] == 'expired' or sla['solution_status'] == 'expired'):
            expired_count += 1

    notifications = []
    if current_user.role == 'manager':
        notifications = Notification.query.filter_by(is_read=False).order_by(Notification.created_at.desc()).limit(10).all()

    return render_template('dashboard.html', requests=requests_list, expired_count=expired_count,
                         notifications=notifications, status_filter=status_filter,
                         priority_filter=priority_filter, search_query=search_query)


@app.route('/sla-settings', methods=['GET', 'POST'])
@login_required
def sla_settings():
    if current_user.role != 'manager':
        flash('Только руководитель может менять настройки SLA', 'danger')
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        for priority in ['low', 'medium', 'high']:
            reaction = request.form.get(f'reaction_{priority}')
            solution = request.form.get(f'solution_{priority}')
            if reaction and solution:
                config = SLAConfig.query.filter_by(priority=priority).first()
                if config:
                    config.reaction_hours = float(reaction)
                    config.solution_hours = float(solution)
                else:
                    config = SLAConfig(priority=priority, reaction_hours=float(reaction), solution_hours=float(solution))
                    db.session.add(config)
        log_action(current_user, 'Изменение настроек SLA')
        db.session.commit()
        flash('Настройки SLA обновлены!', 'success')
        return redirect(url_for('dashboard'))
    sla_settings_dict = {}
    for priority in ['low', 'medium', 'high']:
        config = SLAConfig.query.filter_by(priority=priority).first()
        if config:
            sla_settings_dict[priority] = {'reaction': config.reaction_hours, 'solution': config.solution_hours}
        else:
            defaults = {'high': (0.5, 2), 'medium': (2, 8), 'low': (8, 24)}
            sla_settings_dict[priority] = {'reaction': defaults[priority][0], 'solution': defaults[priority][1]}
    return render_template('sla_settings.html', sla_settings=sla_settings_dict)


@app.route('/clients')
@login_required
def clients_list():
    clients = Client.query.order_by(Client.name).all()
    return render_template('clients_list.html', clients=clients)


@app.route('/clients/add', methods=['GET', 'POST'])
@login_required
def clients_add():
    if current_user.role == 'engineer':
        flash('Инженер не может добавлять клиентов', 'danger')
        return redirect(url_for('clients_list'))
    if request.method == 'POST':
        name = request.form.get('name')
        contact_person = request.form.get('contact_person')
        phone = request.form.get('phone')
        email = request.form.get('email')
        address = request.form.get('address')
        contract_number = request.form.get('contract_number')
        if not all([name, contact_person, phone, email]):
            flash('Заполните обязательные поля', 'danger')
            return redirect(url_for('clients_add'))
        client = Client(name=name, contact_person=contact_person, phone=phone, email=email,
                       address=address, contract_number=contract_number)
        db.session.add(client)
        log_action(current_user, 'Добавление клиента', f'Клиент: {name}')
        db.session.commit()
        flash(f'Клиент {name} добавлен!', 'success')
        return redirect(url_for('clients_list'))
    return render_template('clients_add.html')


@app.route('/clients/<int:client_id>/edit', methods=['GET', 'POST'])
@login_required
def clients_edit(client_id):
    if current_user.role == 'engineer':
        flash('Инженер не может редактировать клиентов', 'danger')
        return redirect(url_for('clients_list'))
    client = db.session.get(Client, client_id)
    if not client:
        flash('Клиент не найден', 'danger')
        return redirect(url_for('clients_list'))
    if request.method == 'POST':
        client.name = request.form.get('name')
        client.contact_person = request.form.get('contact_person')
        client.phone = request.form.get('phone')
        client.email = request.form.get('email')
        client.address = request.form.get('address')
        client.contract_number = request.form.get('contract_number')
        client.is_active = request.form.get('is_active') == 'on'
        log_action(current_user, 'Редактирование клиента', f'Клиент ID={client_id}')
        db.session.commit()
        flash('Данные клиента обновлены!', 'success')
        return redirect(url_for('clients_list'))
    return render_template('clients_edit.html', client=client)


@app.route('/reports')
@login_required
def reports():
    if current_user.role == 'engineer':
        flash('У инженера нет доступа к отчётам', 'danger')
        return redirect(url_for('dashboard'))

    report_type = request.args.get('type', 'statuses')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')

    query = Request.query
    if date_from:
        query = query.filter(Request.created_at >= datetime.strptime(date_from, '%Y-%m-%d'))
    if date_to:
        query = query.filter(Request.created_at <= datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1))

    requests_data = query.order_by(Request.created_at.desc()).all()

    total = len(requests_data)
    new_count = sum(1 for r in requests_data if r.status == 'new')
    in_progress_count = sum(1 for r in requests_data if r.status == 'in_progress')
    resolved_count = sum(1 for r in requests_data if r.status == 'resolved')
    closed_count = sum(1 for r in requests_data if r.status == 'closed')
    expired_count = sum(1 for r in requests_data if r.status != 'closed' and r.get_sla_info()['solution_status'] == 'expired')

    engineer_stats = {}
    for r in requests_data:
        if r.engineer_obj:
            eng_name = r.engineer_obj.full_name
            if eng_name not in engineer_stats:
                engineer_stats[eng_name] = {'total': 0, 'closed': 0, 'expired': 0}
            engineer_stats[eng_name]['total'] += 1
            if r.status == 'closed':
                engineer_stats[eng_name]['closed'] += 1
            if r.status != 'closed' and r.get_sla_info()['solution_status'] == 'expired':
                engineer_stats[eng_name]['expired'] += 1

    priority_stats = {'high': 0, 'medium': 0, 'low': 0}
    for r in requests_data:
        priority_stats[r.priority] += 1

    return render_template('reports.html', report_type=report_type, date_from=date_from, date_to=date_to,
                         total=total, new_count=new_count, in_progress_count=in_progress_count,
                         resolved_count=resolved_count, closed_count=closed_count, expired_count=expired_count,
                         engineer_stats=engineer_stats, priority_stats=priority_stats, requests=requests_data)


@app.route('/export')
@login_required
def export_requests():
    if current_user.role == 'engineer':
        flash('Нет доступа', 'danger')
        return redirect(url_for('dashboard'))

    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    status_filter = request.args.get('status', '')

    query = Request.query
    if date_from:
        query = query.filter(Request.created_at >= datetime.strptime(date_from, '%Y-%m-%d'))
    if date_to:
        query = query.filter(Request.created_at <= datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1))
    if status_filter:
        query = query.filter_by(status=status_filter)

    requests_data = query.order_by(Request.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Номер', 'Клиент', 'Описание', 'Приоритет', 'Статус', 'Исполнитель', 'Диспетчер', 'Дата создания', 'Дата обновления'])

    for req in requests_data:
        writer.writerow([
            req.id,
            req.client_obj.name,
            req.description,
            req.get_priority_display(),
            req.get_status_display(),
            req.engineer_obj.full_name if req.engineer_obj else 'Не назначен',
            req.dispatcher_obj.full_name,
            req.created_at.strftime('%d.%m.%Y %H:%M'),
            req.updated_at.strftime('%d.%m.%Y %H:%M')
        ])

    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=f'requests_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    )


@app.route('/users')
@login_required
def users_list():
    if current_user.role != 'manager':
        flash('Только руководитель может просматривать пользователей', 'danger')
        return redirect(url_for('dashboard'))
    users = User.query.order_by(User.full_name).all()
    return render_template('users_list.html', users=users)


@app.route('/users/<int:user_id>/toggle', methods=['POST'])
@login_required
def users_toggle(user_id):
    if current_user.role != 'manager':
        flash('Нет доступа', 'danger')
        return redirect(url_for('dashboard'))
    user = db.session.get(User, user_id)
    if user:
        user.is_active = not user.is_active
        log_action(current_user, 'Блокировка/разблокировка пользователя', f'Пользователь ID={user_id}, статус={user.is_active}')
        db.session.commit()
        flash(f'Статус пользователя {user.full_name} изменён', 'success')
    return redirect(url_for('users_list'))


@app.route('/users/<int:user_id>/reset-password', methods=['POST'])
@login_required
def users_reset_password(user_id):
    if current_user.role != 'manager':
        flash('Нет доступа', 'danger')
        return redirect(url_for('dashboard'))
    user = db.session.get(User, user_id)
    if user:
        user.set_password('123456')
        log_action(current_user, 'Сброс пароля', f'Пользователь ID={user_id}')
        db.session.commit()
        flash(f'Пароль пользователя {user.full_name} сброшен на 123456', 'success')
    return redirect(url_for('users_list'))


@app.route('/notifications/read/<int:notif_id>')
@login_required
def notifications_read(notif_id):
    notif = db.session.get(Notification, notif_id)
    if notif and notif.user_id == current_user.id:
        notif.is_read = True
        db.session.commit()
    return redirect(url_for('dashboard'))


def init_db():
    with app.app_context():
        db.create_all()

        if User.query.count() == 0:
            u1 = User(username='dispatcher', full_name='Иванов Иван Иванович', role='dispatcher')
            u1.set_password('123456')
            u2 = User(username='engineer', full_name='Петров Петр Петрович', role='engineer')
            u2.set_password('123456')
            u3 = User(username='manager', full_name='Сидоров Сидор Сидорович', role='manager')
            u3.set_password('123456')
            db.session.add_all([u1, u2, u3])
            db.session.commit()

            c1 = Client(name='ООО «Тест-Клиент»', contact_person='Смирнов А.А.',
                       phone='+7 (999) 123-45-67', email='test@client.ru',
                       address='г. Москва, ул. Тестовая, д. 1', contract_number='Д-2026-001')
            db.session.add(c1)
            db.session.commit()

            req = Request(description='Не работает принтер в бухгалтерии', priority='high',
                        status='new', client_id=c1.id, dispatcher_id=u1.id, engineer_id=u2.id,
                        equipment_type='Принтер HP LaserJet', location='Бухгалтерия, 3 этаж')
            db.session.add(req)
            db.session.flush()
            history = StatusHistory(request_id=req.id, old_status=None, new_status='new',
                                   changed_by=u1.id, comment='Заявка создана')
            db.session.add(history)
            db.session.commit()

            print('База данных создана.')


if __name__ == '__main__':
    init_db()
    app.run(debug=True)