import copy
import typing
from contextlib import suppress

from django.db.models import Prefetch, Q
from flag_engine.features.models import FeatureStateModel
from flag_engine.identities.builders import (
    build_identity_dict,
    build_identity_model,
)
from flag_engine.identities.models import IdentityModel
from flag_engine.utils.collections import IdentityFeaturesList

from api_keys.models import MasterAPIKey
from environments.dynamodb import DynamoIdentityWrapper
from environments.models import Environment
from features.models import FeatureState
from features.multivariate.models import MultivariateFeatureStateValue
from users.models import FFAdminUser

from .audit import generate_change_dict
from .tasks import generate_audit_log_records, sync_identity_document_features


class EdgeIdentity:
    dynamo_wrapper = DynamoIdentityWrapper()

    def __init__(self, engine_identity_model: IdentityModel):
        self._engine_identity_model = engine_identity_model
        self._reset_initial_state()

    @classmethod
    def from_identity_document(cls, identity_document: dict) -> "EdgeIdentity":
        return EdgeIdentity(build_identity_model(identity_document))

    @property
    def django_id(self) -> int:
        return self._engine_identity_model.django_id

    @property
    def environment_api_key(self) -> str:
        return self._engine_identity_model.environment_api_key

    @property
    def feature_overrides(self) -> IdentityFeaturesList:
        return self._engine_identity_model.identity_features

    @property
    def id(self) -> typing.Union[int, str]:
        return self._engine_identity_model.django_id or str(
            self._engine_identity_model.identity_uuid
        )

    @property
    def identifier(self) -> str:
        return self._engine_identity_model.identifier

    @property
    def identity_uuid(self) -> str:
        return self._engine_identity_model.identity_uuid

    def add_feature_override(self, feature_state: FeatureStateModel) -> None:
        self._engine_identity_model.identity_features.append(feature_state)

    def get_all_feature_states(
        self,
    ) -> typing.Tuple[
        typing.List[typing.Union[FeatureState, FeatureStateModel]], typing.Set[str]
    ]:
        """
        Get all feature states for a flag engine identity model. The list returned by
        this function contains two distinct types: features.models.FeatureState &
        flag_engine.features.models.FeatureStateModel.

        :return: tuple of (list of feature states, set of feature names that were overridden
            for the identity specifically)
        """
        segment_ids = self.dynamo_wrapper.get_segment_ids(
            identity_model=self._engine_identity_model
        )
        django_environment = Environment.objects.get(api_key=self.environment_api_key)

        q = (
            Q(version__isnull=False)
            & Q(identity__isnull=True)
            & (
                Q(feature_segment__segment__id__in=segment_ids)
                | Q(feature_segment__isnull=True)
            )
        )
        environment_and_segment_feature_states = (
            django_environment.feature_states.select_related(
                "feature",
                "feature_segment",
                "feature_segment__segment",
                "feature_state_value",
            )
            .prefetch_related(
                Prefetch(
                    "multivariate_feature_state_values",
                    queryset=MultivariateFeatureStateValue.objects.select_related(
                        "multivariate_feature_option"
                    ),
                )
            )
            .filter(q)
        )

        feature_states = {}
        for feature_state in environment_and_segment_feature_states:
            feature_name = feature_state.feature.name
            if (
                feature_name not in feature_states
                or feature_state > feature_states[feature_name]
            ):
                feature_states[feature_name] = feature_state

        identity_feature_states = self.feature_overrides
        identity_feature_names = set()
        for identity_feature_state in identity_feature_states:
            feature_name = identity_feature_state.feature.name
            feature_states[feature_name] = identity_feature_state
            identity_feature_names.add(feature_name)

        return list(feature_states.values()), identity_feature_names

    def get_feature_state_by_feature_name_or_id(
        self, feature: typing.Union[str, int]
    ) -> typing.Optional[FeatureStateModel]:
        def match_feature_state(fs):
            if isinstance(feature, int):
                return fs.feature.id == feature
            return fs.feature.name == feature

        feature_state = next(
            filter(
                match_feature_state,
                self._engine_identity_model.identity_features,
            ),
            None,
        )

        return feature_state

    def get_feature_state_by_featurestate_uuid(
        self, featurestate_uuid: str
    ) -> typing.Optional[FeatureStateModel]:
        return next(
            filter(
                lambda fs: fs.featurestate_uuid == featurestate_uuid,
                self._engine_identity_model.identity_features,
            ),
            None,
        )

    def get_hash_key(self, use_mv_v2_evaluation: bool) -> str:
        return self._engine_identity_model.get_hash_key(use_mv_v2_evaluation)

    def remove_feature_override(self, feature_state: FeatureStateModel) -> None:
        with suppress(ValueError):  # ignore if feature state didn't exist
            self._engine_identity_model.identity_features.remove(feature_state)

    def save(self, user: FFAdminUser = None, master_api_key: MasterAPIKey = None):
        self.dynamo_wrapper.put_item(self.to_document())
        changes = self._get_changes(self._initial_state)
        if changes["feature_overrides"]:
            # TODO: would this be simpler if we put a wrapper around FeatureStateModel instead?
            generate_audit_log_records.delay(
                kwargs={
                    "environment_api_key": self.environment_api_key,
                    "identifier": self.identifier,
                    "user_id": getattr(user, "id", None),
                    "changes": changes,
                    "identity_uuid": str(self.identity_uuid),
                    "master_api_key_id": getattr(master_api_key, "id", None),
                }
            )
        self._reset_initial_state()

    def synchronise_features(self, valid_feature_names: typing.Collection[str]) -> None:
        identity_feature_names = {
            fs.feature.name for fs in self._engine_identity_model.identity_features
        }
        if not identity_feature_names.issubset(valid_feature_names):
            self._engine_identity_model.prune_features(list(valid_feature_names))
            sync_identity_document_features.delay(args=(str(self.identity_uuid),))

    def to_document(self) -> dict:
        return build_identity_dict(self._engine_identity_model)

    def _get_changes(self, previous_instance: "EdgeIdentity") -> dict:
        changes = {}
        feature_changes = changes.setdefault("feature_overrides", {})
        previous_feature_overrides = {
            fs.featurestate_uuid: fs for fs in previous_instance.feature_overrides
        }
        current_feature_overrides = {
            fs.featurestate_uuid: fs for fs in self.feature_overrides
        }

        for uuid_, previous_fs in previous_feature_overrides.items():
            current_matching_fs = current_feature_overrides.get(uuid_)
            if current_matching_fs is None:
                feature_changes[previous_fs.feature.name] = generate_change_dict(
                    change_type="-", identity=self, old=previous_fs
                )
            elif (
                current_matching_fs.enabled != previous_fs.enabled
                or current_matching_fs.get_value(self.id)
                != previous_fs.get_value(self.id)
            ):
                feature_changes[previous_fs.feature.name] = generate_change_dict(
                    change_type="~",
                    identity=self,
                    new=current_matching_fs,
                    old=previous_fs,
                )

        for uuid_, previous_fs in current_feature_overrides.items():
            if uuid_ not in previous_feature_overrides:
                feature_changes[previous_fs.feature.name] = generate_change_dict(
                    change_type="+", identity=self, new=previous_fs
                )

        return changes

    def _reset_initial_state(self):
        self._initial_state = copy.deepcopy(self)
