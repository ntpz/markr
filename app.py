import datetime
import os

from flask import Flask
from flask import jsonify
from flask import g
from flask import abort, render_template

from peewee import (Model, FixedCharField, CharField, ForeignKeyField,
                    DoubleField, FloatField, fn, JOIN)
from playhouse.db_url import connect


DATABASE_URL = os.environ.get('DATABASE_URL')
SECRET_KEY = os.environ.get('SECRET_KEY')
DEBUG = os.environ.get('DEBUG')


if DEBUG:
    import logging
    logger = logging.getLogger('peewee')
    logger.setLevel(logging.DEBUG)
    logger.addHandler(logging.StreamHandler())


app_start = datetime.datetime.utcnow()
dbconn = connect(DATABASE_URL)

app = Flask(__name__)
app.config.from_object(__name__)


class BaseModel(Model):
    class Meta:
        database = dbconn


class Origin(BaseModel):
    code = FixedCharField(max_length=1, primary_key=True)
    apikey = FixedCharField(unique=True, max_length=8)
    title = CharField()
    tick_size = DoubleField()


class Mark(BaseModel):
    origin = ForeignKeyField(Origin, backref='marks', index=False)
    ts = DoubleField()
    dts = DoubleField(null=True)

    @classmethod
    def put(cls, origin, ts):
        subq = cls.select(origin.get_id(),
                          ts, ts - fn.MAX(cls.ts)
                          ).where(cls.origin_id == origin.get_id())
        res = Mark.insert_from(subq, cls._meta.sorted_fields[1:])
        return res.execute()

    @classmethod
    def put(cls, apikey, ts):
        uts = ts.timestamp()
        dts_subq = cls.select(uts - fn.MAX(cls.ts)).where(cls.origin_id == Origin.code)
        subq = Origin.select(Origin.code, uts, dts_subq).where(Origin.apikey == apikey)
        return cls.insert_from(subq, cls._meta.sorted_fields[1:]).execute()

    @classmethod
    def stats(cls):
        q = cls.select(Origin.code, Origin.title, Origin.tick_size,
                       fn.MIN(cls.ts).alias('min_ts'),
                       fn.MAX(cls.ts).alias('max_ts'),
                       fn.COUNT(cls.id).alias('num_marks'),
                       fn.MIN(cls.dts).alias('min_dts'),
                       fn.MAX(cls.dts).alias('max_dts'),
                       fn.AVG(cls.dts).alias('avg_dts'),
                       fn.STDDEV_POP(cls.dts).alias('std_dts')
                       ).join(Origin).group_by(Origin.code)
        return q.namedtuples()

    class Meta:
        indexes = (
            (('origin', 'ts', 'dts'), False),
        )


@app.before_request
def before_request():
    g.db = dbconn
    g.db.connect()


@app.after_request
def after_request(response):
    g.db.close()
    return response


def get_object_or_error(model, *expressions, http_code=404):
    try:
        return model.get(*expressions)
    except model.DoesNotExist:
        abort(http_code)


@app.template_filter('uts_datetime')
def _jinja2_filter_uts_datetime(ts, fmt=None):
    utcdt = datetime.datetime.utcfromtimestamp(ts)
    return utcdt.strftime(fmt or '%Y-%m-%d %H:%M:%S')


@app.route('/')
def home():
    ctx = {
        'uptime': datetime.datetime.utcnow() - app_start,
        'stats': Mark.stats()
    }
    return render_template('home.html', **ctx)


@app.route('/origin/<code>')
def show_origin(code):
    return render_template('origin.html')


@app.route('/putmark/<apikey>')
def put_mark(apikey):
    ts = datetime.datetime.utcnow()
    insert_id = Mark.put(apikey, ts)

    if insert_id:
        return jsonify(ts=ts, id=insert_id, status='ok'), 201
    else:
        return jsonify(status='error'), 400


@app.route('/purge/<apikey>')
def purge(apikey):
    origin = get_object_or_error(Origin, Origin.apikey == apikey)
    num_rows = Mark.delete().where(Mark.origin == origin).execute()
    return jsonify(rows_removed=num_rows)


def create_tables():
    with dbconn:
        dbconn.create_tables([Origin, Mark])


def drop_tables():
    with dbconn:
        dbconn.drop_tables([Mark, Origin])


if __name__ == '__main__':
    create_tables()
    app.run(host='0.0.0.0', port=8080)
