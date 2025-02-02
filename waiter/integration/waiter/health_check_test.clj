;;
;; Copyright (c) Two Sigma Open Source, LLC
;;
;; Licensed under the Apache License, Version 2.0 (the "License");
;; you may not use this file except in compliance with the License.
;; You may obtain a copy of the License at
;;
;;  http://www.apache.org/licenses/LICENSE-2.0
;;
;; Unless required by applicable law or agreed to in writing, software
;; distributed under the License is distributed on an "AS IS" BASIS,
;; WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
;; See the License for the specific language governing permissions and
;; limitations under the License.
;;
(ns waiter.health-check-test
  (:require [clojure.data.json :as json]
            [clojure.string :as str]
            [clojure.test :refer :all]
            [clojure.walk :as walk]
            [waiter.status-codes :refer :all]
            [waiter.util.client-tools :refer :all]
            [waiter.util.http-utils :as hu]
            [waiter.util.utils :as utils]))

(defn assert-ping-response
  [waiter-url health-check-protocol idle-timeout service-id response & {:keys [query-params] :or {query-params {}}}]
  (assert-response-status response http-200-ok)
  (let [{:keys [ping-response service-description service-state]}
        (some-> response :body json/read-str walk/keywordize-keys)]
    (is (seq service-description) (str service-description))
    (is (= (service-id->service-description waiter-url service-id) service-description))
    (if (nil? idle-timeout)
      (do
        (is (= "received-response" (get ping-response :result)) (str ping-response))
        (is (= (hu/backend-protocol->http-version health-check-protocol)
               (get-in ping-response [:headers :x-kitchen-protocol-version]))
            (str ping-response))
        (is (= "get" (get-in ping-response [:headers :x-kitchen-request-method])) (str ping-response))
        (if (utils/param-contains? query-params "exclude" "service-state")
          (is (= {:result "excluded" :service-id service-id} service-state))
          (is (= {:exists? true :healthy? true :service-id service-id :status "Running"} service-state))))
      (do
        (is (= "timed-out" (get ping-response :result)) (str ping-response))
        (is (= {:exists? true :healthy? false :service-id service-id :status "Starting"} service-state))))))

(defn run-ping-service-test
  [waiter-url idle-timeout command backend-proto health-check-proto num-ports health-check-port-index
   & {:keys [query-params] :or {query-params {}}}]
  (let [headers (cond-> {:accept "application/json"
                         :x-waiter-cmd command
                         :x-waiter-debug true
                         :x-waiter-health-check-url "/status?include=request-info"
                         :x-waiter-name (rand-name)}
                  backend-proto (assoc :x-waiter-backend-proto backend-proto)
                  health-check-port-index (assoc :x-waiter-health-check-port-index health-check-port-index)
                  health-check-proto (assoc :x-waiter-health-check-proto health-check-proto)
                  idle-timeout (assoc :x-waiter-timeout idle-timeout)
                  num-ports (assoc :x-waiter-ports num-ports))
        {:keys [headers] :as response} (make-kitchen-request waiter-url headers
                                                             :method :post :path "/waiter-ping" :query-params query-params)
        service-id (get headers "x-waiter-service-id")
        health-check-protocol (or health-check-proto backend-proto "http")]
    (with-service-cleanup
      service-id
      (assert-ping-response waiter-url health-check-protocol idle-timeout service-id response :query-params query-params))))

(deftest ^:parallel ^:integration-fast test-basic-ping-service
  (testing-using-waiter-url
    (let [idle-timeout nil
          command (kitchen-cmd "-p $PORT0")
          backend-proto nil
          health-check-proto nil
          num-ports nil
          health-check-port-index nil]
      (run-ping-service-test waiter-url idle-timeout command backend-proto health-check-proto num-ports health-check-port-index))))

(deftest ^:parallel ^:integration-fast test-basic-ping-service-exclude-service-state
  (testing-using-waiter-url
    (let [idle-timeout nil
          command (kitchen-cmd "-p $PORT0")
          backend-proto nil
          health-check-proto nil
          num-ports nil
          health-check-port-index nil]
      (run-ping-service-test waiter-url idle-timeout command backend-proto health-check-proto num-ports health-check-port-index
                             :query-params {"exclude" "service-state"}))))

(deftest ^:parallel ^:integration-fast test-invalid-backend-proto-health-check-proto-combo
  (testing-using-waiter-url
    (let [supported-protocols #{"http" "https" "h2c" "h2"}]
      (doseq [backend-proto supported-protocols
              health-check-proto (disj supported-protocols backend-proto)
              health-check-port-index [nil 0]]
        (let [request-headers (cond-> {:x-waiter-backend-proto backend-proto
                                       :x-waiter-health-check-proto health-check-proto
                                       :x-waiter-name (rand-name)}
                                health-check-port-index (assoc :x-waiter-health-check-port-index health-check-port-index))
              {:keys [body] :as response} (make-kitchen-request waiter-url request-headers)
              error-msg (str "The backend-proto (" backend-proto ") and health check proto (" health-check-proto
                             ") must match when health-check-port-index is zero")]
          (assert-response-status response http-400-bad-request)
          (is (str/includes? (str body) error-msg)))))))

(deftest ^:parallel ^:integration-fast test-ping-http-http-port0-timeout
  (testing-using-waiter-url
    (let [idle-timeout 20000
          command (kitchen-cmd "-p $PORT0 --start-up-sleep-ms 600000")
          backend-proto "http"
          health-check-proto "http"
          num-ports nil
          health-check-port-index nil]
      (run-ping-service-test waiter-url idle-timeout command backend-proto health-check-proto num-ports health-check-port-index))))

(deftest ^:parallel ^:integration-fast test-ping-http-http-port0
  (testing-using-waiter-url
    (let [command (kitchen-cmd "-p $PORT0")
          backend-proto "http"
          health-check-proto "http"
          num-ports nil
          health-check-port-index nil
          idle-timeout nil]
      (run-ping-service-test waiter-url idle-timeout command backend-proto health-check-proto num-ports health-check-port-index))))

(deftest ^:parallel ^:integration-fast test-ping-http-http-port2
  (testing-using-waiter-url
    (let [command (kitchen-cmd "-p $PORT2")
          backend-proto "http"
          health-check-proto "http"
          num-ports 3
          health-check-port-index 2
          idle-timeout nil]
      (run-ping-service-test waiter-url idle-timeout command backend-proto health-check-proto num-ports health-check-port-index))))

(deftest ^:parallel ^:integration-fast test-ping-h2c-http-port1
  (testing-using-waiter-url
    (let [command (kitchen-cmd "-p $PORT1")
          backend-proto "h2c"
          health-check-proto "http"
          num-ports 3
          health-check-port-index 1
          idle-timeout nil]
      (run-ping-service-test waiter-url idle-timeout command backend-proto health-check-proto num-ports health-check-port-index))))

(deftest ^:parallel ^:integration-fast test-ping-h2c-http-port2
  (testing-using-waiter-url
    (let [command (kitchen-cmd "-p $PORT2")
          backend-proto "h2c"
          health-check-proto "http"
          num-ports 3
          health-check-port-index 2
          idle-timeout nil]
      (run-ping-service-test waiter-url idle-timeout command backend-proto health-check-proto num-ports health-check-port-index))))

(deftest ^:parallel ^:integration-fast test-ping-with-fallback-enabled
  (testing-using-waiter-url
    (let [token (rand-name)
          request-headers {:x-waiter-debug true
                           :x-waiter-token token}
          fallback-period-secs 300
          backend-proto "http"
          token-description-1 (-> (kitchen-request-headers :prefix "")
                                (assoc :backend-proto backend-proto
                                       :fallback-period-secs fallback-period-secs
                                       :health-check-url "/status?include=request-info"
                                       :idle-timeout-mins 1
                                       :name (str token "-v1")
                                       :permitted-user "*"
                                       :run-as-user (retrieve-username)
                                       :token token
                                       :version "version-1"))]
      (try
        (assert-response-status (post-token waiter-url token-description-1) http-200-ok)
        (let [ping-response-1 (make-request waiter-url "/waiter-ping" :headers request-headers)
              service-id-1 (get-in ping-response-1 [:headers "x-waiter-service-id"])]
          (with-service-cleanup
            service-id-1
            (assert-ping-response waiter-url backend-proto nil service-id-1 ping-response-1)
            (let [token-description-2 (-> token-description-1
                                        (assoc :name (str token "-v2") :version "version-2")
                                        (update :cmd (fn [c] (str "sleep 10 && " c))))
                  _ (assert-response-status (post-token waiter-url token-description-2) http-200-ok)
                  ping-response-1b (make-request waiter-url "/waiter-ping" :headers request-headers :query-params {"include" "fallback"})
                  service-id-1b (get-in ping-response-1b [:headers "x-waiter-service-id"])
                  _ (is (= service-id-1 service-id-1b))
                  ping-response-2 (make-request waiter-url "/waiter-ping" :headers request-headers)
                  service-id-2 (get-in ping-response-2 [:headers "x-waiter-service-id"])]
              (is (not= service-id-1 service-id-2))
              (with-service-cleanup
                service-id-2
                (assert-ping-response waiter-url backend-proto nil service-id-2 ping-response-2)
                (let [ping-response-2b (make-request waiter-url "/waiter-ping" :headers request-headers :query-params {"include" "fallback"})
                      service-id-2b (get-in ping-response-2b [:headers "x-waiter-service-id"])]
                  (is (= service-id-2 service-id-2b)))))))
        (finally
          (delete-token-and-assert waiter-url token))))))

(deftest ^:parallel ^:integration-fast test-ping-for-run-as-requester
  (testing-using-waiter-url
    (let [token (rand-name)
          request-headers {:x-waiter-debug true
                           :x-waiter-token token}
          backend-proto "http"
          token-description (-> (kitchen-request-headers :prefix "")
                              (assoc :backend-proto backend-proto
                                     :health-check-url "/status?include=request-info"
                                     :idle-timeout-mins 1
                                     :name token
                                     :permitted-user "*"
                                     :run-as-user "*"
                                     :token token
                                     :version "version-foo"))]
      (try
        (assert-response-status (post-token waiter-url token-description) http-200-ok)
        (let [ping-response (make-request waiter-url "/waiter-ping" :headers request-headers)
              service-id (get-in ping-response [:headers "x-waiter-service-id"])]
          (with-service-cleanup
            service-id
            (assert-ping-response waiter-url backend-proto nil service-id ping-response)
            (let [{:keys [permitted-user run-as-user]} (service-id->service-description waiter-url service-id)
                  current-user (retrieve-username)]
              (is (= current-user permitted-user))
              (is (= current-user run-as-user)))))
        (finally
          (delete-token-and-assert waiter-url token))))))

(deftest ^:parallel ^:integration-fast test-temporarily-unhealthy-instance
  (testing-using-waiter-url
    (let [{:keys [cookies instance-id request-headers service-id] :as canary-response}
          (make-request-with-debug-info
            {:x-waiter-cmd (kitchen-cmd "--enable-status-change -p $PORT0")
             :x-waiter-concurrency-level 128
             :x-waiter-health-check-interval-secs 5
             :x-waiter-health-check-max-consecutive-failures 10
             :x-waiter-min-instances 1
             :x-waiter-name (rand-name)}
            #(make-kitchen-request waiter-url % :path "/hello"))
          check-filtered-instances (fn [target-url healthy-filter-fn]
                                     (let [instance-ids (->> (active-instances target-url service-id :cookies cookies)
                                                          (healthy-filter-fn :healthy?)
                                                          (map :id))]
                                       (and (= 1 (count instance-ids))
                                            (= instance-id (first instance-ids)))))]
      (assert-response-status canary-response http-200-ok)
      (with-service-cleanup
        service-id
        (doseq [[_ router-url] (routers waiter-url)]
          (is (wait-for #(check-filtered-instances router-url filter)))
          (is (= 1 (count (active-instances router-url service-id :cookies cookies)))))
        (let [request-headers (assoc request-headers
                                :x-kitchen-default-status-timeout 20000
                                :x-kitchen-default-status-value http-400-bad-request)
              response (make-kitchen-request waiter-url request-headers :path "/hello")]
          (assert-response-status response http-400-bad-request))
        (doseq [[_ router-url] (routers waiter-url)]
          (is (wait-for #(check-filtered-instances router-url remove)))
          (is (= 1 (count (active-instances router-url service-id :cookies cookies)))))
        (let [response (make-kitchen-request waiter-url request-headers :path "/hello")]
          (assert-response-status response http-200-ok))
        (doseq [[_ router-url] (routers waiter-url)]
          (is (wait-for #(check-filtered-instances router-url filter)))
          (is (= 1 (count (active-instances router-url service-id :cookies cookies)))))))))

(deftest ^:parallel ^:integration-fast test-standard-health-check-authentication
  (testing-using-waiter-url
    (when-not (using-marathon? waiter-url)
      (let [token (rand-name)
            request-headers {:x-waiter-debug true
                             :x-waiter-token token}
            backend-proto "http"
            token-description (-> (kitchen-request-headers :prefix "")
                                (assoc :backend-proto backend-proto
                                       :cmd (kitchen-cmd "--enable-health-check-authentication -p $PORT0")
                                       :health-check-authentication "standard"
                                       :health-check-interval-secs 5
                                       :health-check-max-consecutive-failures 4
                                       :health-check-url "/status"
                                       :idle-timeout-mins 1
                                       :name token
                                       :permitted-user "*"
                                       :run-as-user "*"
                                       :token token
                                       :version "version-foo"))]
        (try
          (assert-response-status (post-token waiter-url token-description) http-200-ok)
          (let [ping-response (make-request waiter-url "/waiter-ping" :headers request-headers :method :get)
                service-id (get-in ping-response [:headers "x-waiter-service-id"])]
            (is service-id (str ping-response))
            (with-service-cleanup
              service-id
              (let [backend-response (-> ping-response :body json/read-str walk/keywordize-keys :ping-response)]
                (assert-response-status backend-response http-200-ok)
                (is (= (str "Hello " (retrieve-username)) (-> backend-response :body str))))))
          (finally
            (delete-token-and-assert waiter-url token)))))))
