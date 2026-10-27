package com.demo.tamper.repository;

import com.demo.tamper.model.entity.FrameResult;
import java.util.List;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.transaction.annotation.Transactional;

public interface FrameResultRepository extends JpaRepository<FrameResult, Long> {

    List<FrameResult> findByTaskIdOrderByFrameIndexAscAlgorithmAsc(String taskId);

    List<FrameResult> findByTaskIdAndAlgorithmOrderByFrameIndexAsc(String taskId, String algorithm);

    Page<FrameResult> findByTaskIdOrderByFrameIndexAscAlgorithmAsc(String taskId, Pageable pageable);

    Page<FrameResult> findByTaskIdAndAlgorithmOrderByFrameIndexAsc(
            String taskId, String algorithm, Pageable pageable);

    long countByTaskId(String taskId);

    @Query("select distinct f.algorithm from FrameResult f where f.taskId = ?1 order by f.algorithm")
    List<String> findAlgorithmsByTaskId(String taskId);

    @Query("select f from FrameResult f where f.taskId = ?1 and f.algorithm = ?2 "
            + "and f.frameIndex = ?3")
    List<FrameResult> findOneResult(String taskId, String algorithm, Integer frameIndex);

    @Transactional
    @Modifying
    void deleteByTaskId(String taskId);
}
