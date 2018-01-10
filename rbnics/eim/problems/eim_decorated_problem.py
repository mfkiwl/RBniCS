# Copyright (C) 2015-2018 by the RBniCS authors
#
# This file is part of RBniCS.
#
# RBniCS is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# RBniCS is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with RBniCS. If not, see <http://www.gnu.org/licenses/>.
#

from itertools import product as cartesian_product
from rbnics.backends import ParametrizedExpressionFactory, SeparatedParametrizedForm, SymbolicParameters
from rbnics.utils.decorators import overload, PreserveClassName, ProblemDecoratorFor, tuple_of
from rbnics.eim.utils.io import AffineExpansionSeparatedFormsStorage
from rbnics.eim.problems.eim_approximation import EIMApproximation
from rbnics.eim.problems.time_dependent_eim_approximation import TimeDependentEIMApproximation

def ExactEIMAlgorithm(**kwargs):
    # Enable exact parametrized functions evaluation both offline and online
    from rbnics.eim.problems.exact_parametrized_functions import ExactParametrizedFunctions
    kwargs["stages"] = ("offline", "online")
    return ExactParametrizedFunctions(**kwargs)

def EIMDecoratedProblem(
    stages=("offline", "online"),
    basis_generation="Greedy",
    **decorator_kwargs
):
    from rbnics.eim.problems.eim import EIM
    
    @ProblemDecoratorFor(EIM, ExactAlgorithm=ExactEIMAlgorithm, stages=stages, basis_generation=basis_generation)
    def EIMDecoratedProblem_Decorator(ParametrizedDifferentialProblem_DerivedClass):
                
        @PreserveClassName
        class EIMDecoratedProblem_Class(ParametrizedDifferentialProblem_DerivedClass):
            
            # Default initialization of members
            def __init__(self, V, **kwargs):
                # Call the parent initialization
                ParametrizedDifferentialProblem_DerivedClass.__init__(self, V, **kwargs)
                # Storage for symbolic parameters
                self.mu_symbolic = None
                # Storage for EIM reduced problems
                self.separated_forms = dict() # from terms to AffineExpansionSeparatedFormsStorage
                self.EIM_approximations = dict() # from coefficients to EIMApproximation
                
                # Store value of N_EIM passed to solve
                self._N_EIM = None
                # Store values passed to decorator
                self._store_EIM_stages(stages)
                # Avoid useless assignments
                self._update_N_EIM__previous_kwargs = None
                
            @overload(str)
            def _store_EIM_stages(self, stage):
                assert stages != "offline", "This choice does not make any sense because it requires an EIM offline stage which then is not used online"
                assert stages == "online"
                self._apply_EIM_at_stages = (stages, )
                
            @overload(tuple_of(str))
            def _store_EIM_stages(self, stage):
                assert len(stages) in (1, 2)
                assert stages[0] in ("offline", "online")
                if len(stages) > 1:
                    assert stages[1] in ("offline", "online")
                    assert stages[0] != stages[1]
                self._apply_EIM_at_stages = stages
                
            def _init_EIM_approximations(self):
                # Preprocess each term in the affine expansions.
                # Note that this cannot be done in __init__, because operators may depend on self.mu,
                # which is not defined at __init__ time. Moreover, it cannot be done either by init,
                # because the init method is called by offline stage of the reduction method instance,
                # but we need EIM approximations to be already set up at the time the reduction method
                # instance is built. Thus, we will call this method in the reduction method instance
                # constructor (having a safeguard in place to avoid repeated calls).
                assert (
                    (len(self.separated_forms) == 0)
                        ==
                    (len(self.EIM_approximations) == 0)
                )
                if len(self.EIM_approximations) == 0: # initialize EIM approximations only once
                    # Initialize symbolic parameters only once (may be shared between EIM and exact interpolation)
                    if self.mu_symbolic is None:
                        self.mu_symbolic = SymbolicParameters(self, self.V, self.mu)
                    # Temporarily replace float parameters with symbols, so that we can detect if operators
                    # are parametrized
                    mu_float = self.mu
                    self.mu = self.mu_symbolic
                    # Loop over each term
                    for term in self.terms:
                        try:
                            forms = ParametrizedDifferentialProblem_DerivedClass.assemble_operator(self, term)
                        except ValueError: # possibily raised e.g. because output computation is optional
                            pass
                        else:
                            Q = len(forms)
                            self.separated_forms[term] = AffineExpansionSeparatedFormsStorage(Q)
                            for q in range(Q):
                                self.separated_forms[term][q] = SeparatedParametrizedForm(forms[q])
                                self.separated_forms[term][q].separate()
                                # All parametrized coefficients should be approximated by EIM
                                for (addend_index, addend) in enumerate(self.separated_forms[term][q].coefficients):
                                    for (factor, factor_name) in zip(addend, self.separated_forms[term][q].placeholders_names(addend_index)):
                                        if factor not in self.EIM_approximations:
                                            factory_factor = ParametrizedExpressionFactory(factor)
                                            if factory_factor.is_time_dependent():
                                                EIMApproximationType = TimeDependentEIMApproximation
                                            else:
                                                EIMApproximationType = EIMApproximation
                                            self.EIM_approximations[factor] = EIMApproximationType(self, factory_factor, self.name() + "/eim/" + factor_name, basis_generation)
                    # Restore float parameters
                    self.mu = mu_float
                
            def _solve(self, **kwargs):
                self._update_N_EIM(**kwargs)
                ParametrizedDifferentialProblem_DerivedClass._solve(self, **kwargs)
            
            def _update_N_EIM(self, **kwargs):
                if kwargs != self._update_N_EIM__previous_kwargs:
                    if "EIM" in kwargs:
                        self._N_EIM = dict()
                        N_EIM = kwargs["EIM"]
                        for term in self.separated_forms:
                            self._N_EIM[term] = list()
                            if isinstance(N_EIM, dict):
                                assert term in N_EIM
                                assert len(N_EIM[term]) == len(self.separated_forms[term])
                                for N_eim_term_q in N_EIM[term]:
                                    self._N_EIM[term].append(N_eim_term_q)
                            else:
                                assert isinstance(N_EIM, int)
                                for _ in self.separated_forms[term]:
                                    self._N_EIM[term].append(N_EIM)
                    else:
                        self._N_EIM = None
                    self._update_N_EIM__previous_kwargs = kwargs
                
            def assemble_operator(self, term):
                if term in self.separated_forms.keys():
                    if "offline" in self._apply_EIM_at_stages:
                        return self._assemble_operator_EIM(term)
                    else:
                        return ParametrizedDifferentialProblem_DerivedClass.assemble_operator(self, term)
                else:
                    return ParametrizedDifferentialProblem_DerivedClass.assemble_operator(self, term) # may raise an exception
                    
            def _assemble_operator_EIM(self, term):
                eim_forms = list()
                for form in self.separated_forms[term]:
                    # Append forms computed with EIM, if applicable
                    for (index, addend) in enumerate(form.coefficients):
                        replacements__list = list()
                        for factor in addend:
                            replacements__list.append(self.EIM_approximations[factor].basis_functions)
                        replacements__cartesian_product = cartesian_product(*replacements__list)
                        for new_coeffs in replacements__cartesian_product:
                            eim_forms.append(
                                form.replace_placeholders(index, new_coeffs)
                            )
                    # Append forms which did not require EIM, if applicable
                    for unchanged_form in form.unchanged_forms:
                        eim_forms.append(unchanged_form)
                return tuple(eim_forms)
                    
            def compute_theta(self, term):
                if term in self.separated_forms.keys():
                    if "offline" in self._apply_EIM_at_stages:
                        return self._compute_theta_EIM(term)
                    else:
                        return ParametrizedDifferentialProblem_DerivedClass.compute_theta(self, term)
                else:
                    return ParametrizedDifferentialProblem_DerivedClass.compute_theta(self, term) # may raise an exception
                    
            def _compute_theta_EIM(self, term):
                original_thetas = ParametrizedDifferentialProblem_DerivedClass.compute_theta(self, term)
                eim_thetas = list()
                assert len(self.separated_forms[term]) == len(original_thetas)
                if self._N_EIM is not None:
                    assert term in self._N_EIM
                    assert len(self.separated_forms[term]) == len(self._N_EIM[term])
                for (q, (form, original_theta)) in enumerate(zip(self.separated_forms[term], original_thetas)):
                    # Append coefficients computed with EIM, if applicable
                    for addend in form.coefficients:
                        eim_thetas__list = list()
                        for factor in addend:
                            N_EIM = None
                            if self._N_EIM is not None:
                                N_EIM = self._N_EIM[term][q]
                            eim_thetas__list.append(self.EIM_approximations[factor].compute_interpolated_theta(N_EIM))
                        eim_thetas__cartesian_product = cartesian_product(*eim_thetas__list)
                        for tuple_ in eim_thetas__cartesian_product:
                            eim_thetas_tuple = original_thetas[q]
                            for eim_thata_factor in tuple_:
                                eim_thetas_tuple *= eim_thata_factor
                            eim_thetas.append(eim_thetas_tuple)
                    # Append coefficients which did not require EIM, if applicable
                    for _ in form.unchanged_forms:
                        eim_thetas.append(original_theta)
                return tuple(eim_thetas)
            
        # return value (a class) for the decorator
        return EIMDecoratedProblem_Class
        
    # return the decorator itself
    return EIMDecoratedProblem_Decorator
