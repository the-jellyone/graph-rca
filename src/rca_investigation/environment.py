"""Public investigation transitions. Independent scorers own labels."""
from .catalogue import catalogue_hash, mask
from .contracts import Decision
from .ledger import EvidenceLedger


class InvestigationEnv:
    def __init__(self, topology, specs, model, backend, *, defer_utility=0.25, correct_utility=1, incorrect_utility=0):
        self.topology, self.specs, self.model, self.backend = topology, tuple(specs), model, backend
        if tuple(s.action_id for s in specs) != model.action_ids:
            raise ValueError('model action mapping mismatch')
        catalogue_hash(self.specs)
        self.defer_utility, self.correct_utility, self.incorrect_utility = defer_utility, correct_utility, incorrect_utility
        self.incident = None

    def reset(self, incident):
        incident.validate_context(self.topology, self.specs)
        if getattr(incident, 'schema_version', 1) == 3:
            from .belief_v3 import CauseSetModel
            from .contracts_v3 import ObjectiveV3
            from .contracts import digest
            if not isinstance(self.model, CauseSetModel) or self.model.task_id != incident.task_id:
                raise ValueError('v3 requires a matching cause-set diagnostic model')
            objective = ObjectiveV3(lambda_cost=incident.lambda_cost, correct=self.correct_utility,
                                    incorrect=self.incorrect_utility, defer=self.defer_utility)
            if incident.objective_hash != digest(objective.to_dict()):
                raise ValueError('v3 public objective differs from the independent scorer')
        self.backend.begin(incident)
        self.incident = incident
        self.ledger = EvidenceLedger()
        self.history = ()
        self.probes = 0
        self.terminated = False
        self.decision = None
        self.probe_return = 0.0
        self.capabilities = self.backend.capabilities(self.topology)
        return self.observation()

    def observation(self):
        if self.incident is None:
            raise RuntimeError('reset first')
        remaining = self.incident.budget - self.ledger.credits
        probes_left = self.incident.max_probes - self.probes
        result = {'topology': self.topology.to_dict(), 'entry_resource': self.incident.entry_resource,
                'entry_protocol': self.incident.entry_protocol, 'catalogue_hash': self.incident.catalogue_hash,
                'history': self.history, 'diagnoses': self.model.diagnosis(self.history),
                'prices': {s.action_id: s.credits for s in self.specs}, 'remaining_credits': remaining,
                'remaining_probes': probes_left, 'lambda_cost': self.incident.lambda_cost,
                'mask': mask(self.specs, self.ledger.acquired_actions, remaining, probes_left, self.capabilities, self.terminated)}
        result['objective'] = {'defer': self.defer_utility, 'correct': self.correct_utility, 'incorrect': self.incorrect_utility}
        if getattr(self.incident, 'schema_version', 1) in (2, 3):
            result.update(schema_version=self.incident.schema_version, task_id=self.incident.task_id, topology_hash=self.topology.hash,
                          encoding_hash=self.incident.encoding_hash, objective_hash=self.incident.objective_hash,
                          alert_context=self.incident.alert_context_json)
        if getattr(self.incident, 'schema_version', 1) == 3:
            result['model_hash'] = self.model.hash
        return result

    def terminal_action(self):
        probabilities = self.model.diagnosis(self.history)
        stop = self.incorrect_utility + max(probabilities.values()) * (self.correct_utility-self.incorrect_utility)
        return 'DEFER' if self.defer_utility >= stop else 'STOP'

    def _finish(self, action, reason, policy_hash):
        probabilities = self.model.diagnosis(self.history)
        prediction = max(self.model.classes, key=lambda c: probabilities[c])
        if getattr(self.incident, 'schema_version', 1) == 3:
            from .contracts_v3 import DecisionV3
            from .contracts import digest
            # Existing callers may supply descriptive identities. Store their hash.
            from .contracts_v2 import sha256
            try:
                sha256(policy_hash)
            except ValueError:
                policy_hash = digest(policy_hash)
            self.decision = DecisionV3(3, 'DEFER' if action == 'DEFER' else 'DIAGNOSE',
                () if action == 'DEFER' else self.model.decode(prediction),
                0.0 if action == 'DEFER' else probabilities[prediction],
                self.ledger.evidence_ids, policy_hash, self.model.hash, reason)
            self.terminated = True
            return
        self.decision = Decision('DEFER' if action == 'DEFER' else prediction,
                                 0.0 if action == 'DEFER' else probabilities[prediction],
                                 self.ledger.evidence_ids, policy_hash, self.model.hash, reason)
        self.terminated = True

    def step(self, action, *, policy_hash='unspecified'):
        before = self.observation()
        if action not in before['mask']:
            raise ValueError('invalid, repeated, unaffordable or unsupported action')
        if action in ('STOP', 'DEFER'):
            self._finish(action, 'voluntary', policy_hash)
            return self.observation(), 0.0, True
        spec = next(s for s in self.specs if s.action_id == action)
        packet = self.backend.execute(spec)
        if not self.ledger.acquire(spec, packet, self.incident):
            raise RuntimeError('masked duplicate was dispatched')
        self.probes += 1
        self.history += ((action, packet.symbol),)
        reward = -self.incident.lambda_cost * spec.credits
        self.probe_return += reward
        after = self.observation()
        if not any(a not in ('STOP', 'DEFER') for a in after['mask']):
            reason = 'budget' if after['remaining_credits'] == 0 else 'horizon' if after['remaining_probes'] == 0 else 'no_probe_available'
            self._finish(self.terminal_action(), reason, policy_hash)
            after = self.observation()
        return after, reward, self.terminated

    def close(self):
        self.backend.close()


def score_decision(decision, label, *, correct=1.0, incorrect=0.0, defer=0.25):
    if getattr(decision, 'schema_version', 1) == 3:
        from .diagnosis_sets import terminal_utility
        return terminal_utility(decision, label, correct=correct, incorrect=incorrect, defer=defer)
    return defer if decision.prediction == 'DEFER' else correct if decision.prediction == label else incorrect
