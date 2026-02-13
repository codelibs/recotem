"""SQLAlchemy models mirroring Django's database schema (read-only)."""

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class TrainedModel(Base):
    __tablename__ = "api_trainedmodel"

    id = Column(Integer, primary_key=True)
    ins_datetime = Column(DateTime)
    updated_at = Column(DateTime)
    file = Column(String(100), name="file")
    filesize = Column(Integer)
    configuration_id = Column(Integer)
    data_loc_id = Column(Integer)
    irspack_version = Column(String(16))


class Project(Base):
    __tablename__ = "api_project"

    id = Column(Integer, primary_key=True)
    name = Column(String(256))
    owner_id = Column(Integer)
    user_column = Column(String(256))
    item_column = Column(String(256))
    time_column = Column(String(256))


class ApiKey(Base):
    __tablename__ = "api_apikey"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer)
    owner_id = Column(Integer)
    name = Column(String(256))
    key_prefix = Column(String(16))
    hashed_key = Column(String(256))
    scopes = Column(JSON)
    is_active = Column(Boolean)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)


class ModelConfiguration(Base):
    __tablename__ = "api_modelconfiguration"

    id = Column(Integer, primary_key=True)
    name = Column(String(256))
    project_id = Column(Integer)
    recommender_class_name = Column(String(128))
    parameters_json = Column(JSON)


class DeploymentSlot(Base):
    __tablename__ = "api_deploymentslot"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer)
    name = Column(String(256))
    trained_model_id = Column(Integer)
    weight = Column(Float)
    is_active = Column(Boolean)


class TrainingData(Base):
    __tablename__ = "api_trainingdata"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer)
    file = Column(String(100), name="file")
    filesize = Column(Integer)
