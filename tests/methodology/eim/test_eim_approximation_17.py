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

import os
import pytest
from dolfin import assemble, assign, dx, FiniteElement, Function, FunctionSpace, MixedElement, inner, Point, project, RectangleMesh, SpatialCoordinate, sqrt, TestFunction, TrialFunction, VectorElement
from rbnics import EquispacedDistribution
from rbnics.backends import BasisFunctionsMatrix, GramSchmidt, ParametrizedExpressionFactory, ParametrizedTensorFactory, SymbolicParameters, transpose
from rbnics.backends.online import OnlineFunction
from rbnics.eim.problems.eim_approximation import EIMApproximation
from rbnics.eim.reduction_methods.eim_approximation_reduction_method import EIMApproximationReductionMethod
from rbnics.problems.base import ParametrizedProblem
from rbnics.reduction_methods.base import ReductionMethod
from rbnics.utils.decorators import StoreMapFromProblemNameToProblem, StoreMapFromProblemToReducedProblem, StoreMapFromProblemToTrainingStatus, StoreMapFromSolutionToProblem, sync_setters, UpdateMapFromProblemToTrainingStatus

@pytest.mark.parametrize("expression_type", ["Function", "Vector", "Matrix"])
@pytest.mark.parametrize("basis_generation", ["Greedy", "POD"])
def test_eim_approximation_17(expression_type, basis_generation):
    """
    This test is similar to test 15. However, in contrast to test 15, the solution is not split at all.
    * EIM: unsplit solution is used in the definition of the parametrized expression, similarly to test 11.
    * DEIM: unsplit solution is used in the definition of the parametrized tensor. This results in a single coefficient
      of type Function, which however is stored internally by UFL as an Indexed of Function and a mute index. This test
      requires the FEniCS backend to properly differentiate between Indexed objects with a fixed index (such as a component
      of the solution as in test 13) and Indexed objects with a mute index, which should be treated has if the entire solution
      was required.
    """

    @StoreMapFromProblemNameToProblem
    @StoreMapFromProblemToTrainingStatus
    @StoreMapFromSolutionToProblem
    class MockProblem(ParametrizedProblem):
        def __init__(self, V, **kwargs):
            # Call parent
            ParametrizedProblem.__init__(self, os.path.join("test_eim_approximation_17_tempdir", expression_type, basis_generation, "mock_problem"))
            # Minimal subset of a ParametrizedDifferentialProblem
            self.V = V
            self._solution = Function(V)
            self.components = ["u", "s", "p"]
            # Parametrized function to be interpolated
            x = SpatialCoordinate(V.mesh())
            mu = SymbolicParameters(self, V, (-1., -1.))
            self.f00 = 1./sqrt(pow(x[0]-mu[0], 2) + pow(x[1]-mu[1], 2) + 0.01)
            self.f01 = 1./sqrt(pow(x[0]-mu[0], 4) + pow(x[1]-mu[1], 4) + 0.01)
            # Inner product
            f = TrialFunction(self.V)
            g = TestFunction(self.V)
            self.X = assemble(inner(f, g)*dx)
            # Collapsed vector and space
            self.V0 = V.sub(0).collapse()
            self.V00 = V.sub(0).sub(0).collapse()
            self.V1 = V.sub(1).collapse()
            
        def name(self):
            return "MockProblem_17_" + expression_type + "_" + basis_generation
            
        def init(self):
            pass
            
        def solve(self):
            assert not hasattr(self, "_is_solving")
            self._is_solving = True
            f00 = project(self.f00, self.V00)
            f01 = project(self.f01, self.V00)
            assign(self._solution.sub(0).sub(0), f00)
            assign(self._solution.sub(0).sub(1), f01)
            delattr(self, "_is_solving")
            return self._solution

    @UpdateMapFromProblemToTrainingStatus
    class MockReductionMethod(ReductionMethod):
        def __init__(self, truth_problem, **kwargs):
            # Call parent
            ReductionMethod.__init__(self, os.path.join("test_eim_approximation_17_tempdir", expression_type, basis_generation, "mock_problem"))
            # Minimal subset of a DifferentialProblemReductionMethod
            self.truth_problem = truth_problem
            self.reduced_problem = None
            # I/O
            self.folder["basis"] = os.path.join(self.truth_problem.folder_prefix, "basis")
            # Gram Schmidt
            self.GS = GramSchmidt(self.truth_problem.X)
            
        def initialize_training_set(self, ntrain, enable_import=True, sampling=None, **kwargs):
            return ReductionMethod.initialize_training_set(self, self.truth_problem.mu_range, ntrain, enable_import, sampling, **kwargs)
            
        def initialize_testing_set(self, ntest, enable_import=False, sampling=None, **kwargs):
            return ReductionMethod.initialize_testing_set(self, self.truth_problem.mu_range, ntest, enable_import, sampling, **kwargs)
            
        def offline(self):
            self.reduced_problem = MockReducedProblem(self.truth_problem)
            if self.folder["basis"].create(): # basis folder was not available yet
                for (index, mu) in enumerate(self.training_set):
                    self.truth_problem.set_mu(mu)
                    print("solving mock problem at mu =", self.truth_problem.mu)
                    f = self.truth_problem.solve()
                    component = "u" if index % 2 == 0 else "s"
                    self.reduced_problem.basis_functions.enrich(f, component)
                    self.GS.apply(self.reduced_problem.basis_functions[component], 0)
                self.reduced_problem.basis_functions.save(self.folder["basis"], "basis")
            else:
                self.reduced_problem.basis_functions.load(self.folder["basis"], "basis")
            self._finalize_offline()
            return self.reduced_problem
            
        def error_analysis(self, N=None, **kwargs):
            pass
            
        def speedup_analysis(self, N=None, **kwargs):
            pass

    @StoreMapFromProblemToReducedProblem
    class MockReducedProblem(ParametrizedProblem):
        @sync_setters("truth_problem", "set_mu", "mu")
        @sync_setters("truth_problem", "set_mu_range", "mu_range")
        def __init__(self, truth_problem, **kwargs):
            # Call parent
            ParametrizedProblem.__init__(self, os.path.join("test_eim_approximation_17_tempdir", expression_type, basis_generation, "mock_problem"))
            # Minimal subset of a ParametrizedReducedDifferentialProblem
            self.truth_problem = truth_problem
            self.basis_functions = BasisFunctionsMatrix(self.truth_problem.V)
            self.basis_functions.init(self.truth_problem.components)
            self._solution = None
            
        def solve(self):
            print("solving mock reduced problem at mu =", self.mu)
            assert not hasattr(self, "_is_solving")
            self._is_solving = True
            f = self.truth_problem.solve()
            f_N = transpose(self.basis_functions)*self.truth_problem.X*f
            # Return the reduced solution
            self._solution = OnlineFunction(f_N)
            delattr(self, "_is_solving")
            return self._solution
            
    class ParametrizedFunctionApproximation(EIMApproximation):
        def __init__(self, truth_problem, expression_type, basis_generation):
            self.V = truth_problem.V
            #
            folder_prefix = os.path.join("test_eim_approximation_17_tempdir", expression_type, basis_generation)
            assert expression_type in ("Function", "Vector", "Matrix")
            if expression_type == "Function":
                # Call Parent constructor
                EIMApproximation.__init__(self, None, ParametrizedExpressionFactory(truth_problem._solution), folder_prefix, basis_generation)
            elif expression_type == "Vector":
                v = TestFunction(self.V)
                form = inner(truth_problem._solution, v)*dx
                # Call Parent constructor
                EIMApproximation.__init__(self, None, ParametrizedTensorFactory(form), folder_prefix, basis_generation)
            elif expression_type == "Matrix":
                u = TrialFunction(self.V)
                v = TestFunction(self.V)
                form = inner(truth_problem._solution, u)*v[0]*dx
                # Call Parent constructor
                EIMApproximation.__init__(self, None, ParametrizedTensorFactory(form), folder_prefix, basis_generation)
            else: # impossible to arrive here anyway thanks to the assert
                raise AssertionError("Invalid expression_type")

    # 1. Create the mesh for this test
    mesh = RectangleMesh(Point(0.1, 0.1), Point(0.9, 0.9), 20, 20)

    # 2. Create Finite Element space (Lagrange P1)
    element_0 = VectorElement("Lagrange", mesh.ufl_cell(), 2)
    element_1 = FiniteElement("Lagrange", mesh.ufl_cell(), 1)
    element = MixedElement(element_0, element_1)
    V = FunctionSpace(mesh, element, components=[["u", "s"], "p"])

    # 3. Create a parametrized problem
    problem = MockProblem(V)
    mu_range = [(-1., -0.01), (-1., -0.01)]
    problem.set_mu_range(mu_range)

    # 4. Create a reduction method and run the offline phase to generate the corresponding
    #    reduced problem
    reduction_method = MockReductionMethod(problem)
    reduction_method.initialize_training_set(16, sampling=EquispacedDistribution())
    reduction_method.offline()

    # 5. Allocate an object of the ParametrizedFunctionApproximation class
    parametrized_function_approximation = ParametrizedFunctionApproximation(problem, expression_type, basis_generation)
    parametrized_function_approximation.set_mu_range(mu_range)

    # 6. Prepare reduction with EIM
    parametrized_function_reduction_method = EIMApproximationReductionMethod(parametrized_function_approximation)
    parametrized_function_reduction_method.set_Nmax(16)

    # 7. Perform EIM offline phase
    parametrized_function_reduction_method.initialize_training_set(64, sampling=EquispacedDistribution())
    reduced_parametrized_function_approximation = parametrized_function_reduction_method.offline()

    # 8. Perform EIM online solve
    online_mu = (-1., -1.)
    reduced_parametrized_function_approximation.set_mu(online_mu)
    reduced_parametrized_function_approximation.solve()

    # 9. Perform EIM error analysis
    parametrized_function_reduction_method.initialize_testing_set(100)
    parametrized_function_reduction_method.error_analysis()
